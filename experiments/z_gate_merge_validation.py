#!/usr/bin/env python3
"""z-gate 条件付きマージ検証.

HA20の30件 tfidf-miss に対してCALM z-gateを適用し、
条件付きマージ（tfidf優先、miss分のみz救済、構造ゲートfailなら却下）後の
Spearman ρ を検証する。

Usage:
    python experiments/z_gate_merge_validation.py [--device cpu|cuda]
"""
from __future__ import annotations

import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from scipy.stats import spearmanr

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
SEED = 42
CALM_MODEL_ID = "cccczshao/CALM-Autoencoder"
THETAS = [0.45, 0.50, 0.55]

HA20_IDS = [
    "q009", "q012", "q015", "q019", "q024", "q025", "q032", "q033",
    "q037", "q044", "q049", "q061", "q063", "q069", "q071", "q075",
    "q080", "q083", "q095", "q100",
]

np.random.seed(SEED)
torch.manual_seed(SEED)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------
@dataclass
class MissEntry:
    """tfidf miss 1件."""
    question_id: str
    prop_idx: int
    proposition: str
    human_score: float
    structural_fail: bool  # any(f1,f2,f3,f4) > 0
    S: float               # 既存の構造スコア
    z_score: float = 0.0   # CALM cosine similarity


@dataclass
class QuestionData:
    """1問の全情報."""
    question_id: str
    human_score: float
    response: str
    core_propositions: list[str]
    hit_ids: list[int]
    miss_ids: list[int]
    S: float
    f1: float
    f2: float
    f3: float
    f4: float

    @property
    def structural_fail(self) -> bool:
        return any(v > 0 for v in [self.f1, self.f2, self.f3, self.f4])


# ---------------------------------------------------------------------------
# CALM Encoder (experiments/calm_z_matching/ から流用)
# ---------------------------------------------------------------------------
class CALMEncoder:
    """CALM オートエンコーダのエンコーダ部分."""

    def __init__(self, device: str = "cpu"):
        # calm_encoder モジュールのパスを追加
        calm_dir = Path(__file__).resolve().parent / "calm_z_matching"
        if str(calm_dir) not in sys.path:
            sys.path.insert(0, str(calm_dir))

        from calm_encoder.configuration_autoencoder import AutoencoderConfig
        from calm_encoder.modeling_autoencoder import Autoencoder
        from transformers import AutoTokenizer
        from huggingface_hub import hf_hub_download
        import safetensors.torch

        print(f"[CALM] Loading tokenizer from {CALM_MODEL_ID}...")
        self.tokenizer = AutoTokenizer.from_pretrained(CALM_MODEL_ID)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        print(f"[CALM] Loading config from {CALM_MODEL_ID}...")
        config_path = hf_hub_download(CALM_MODEL_ID, "config.json")
        with open(config_path) as f:
            cfg_dict = json.load(f)
        config = AutoencoderConfig(**{
            k: v for k, v in cfg_dict.items()
            if k in AutoencoderConfig.__init__.__code__.co_varnames
        })

        print(f"[CALM] Building model (latent_size={config.latent_size}, "
              f"patch_size={config.patch_size})...")
        self.model = Autoencoder(config)

        weights_path = hf_hub_download(CALM_MODEL_ID, "model.safetensors")
        state_dict = safetensors.torch.load_file(weights_path)
        self.model.load_state_dict(state_dict, strict=False)

        self.device = device
        self.model = self.model.to(device).eval()
        self.patch_size = config.patch_size
        self.latent_size = config.latent_size
        print("[CALM] Ready.")

    @torch.no_grad()
    def encode(self, text: str) -> np.ndarray:
        """テキスト→zベクトル系列 (num_patches, latent_size)."""
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) == 0:
            return np.zeros((1, self.latent_size), dtype=np.float32)

        remainder = len(tokens) % self.patch_size
        if remainder != 0:
            pad_len = self.patch_size - remainder
            pad_id = self.tokenizer.pad_token_id or 0
            tokens = tokens + [pad_id] * pad_len

        input_ids = torch.tensor([tokens], dtype=torch.long, device=self.device)
        latent_states = self.model.encoder(input_ids)
        mean = latent_states[:, :, :self.latent_size]
        return mean.squeeze(0).cpu().numpy()

    def encode_pooled(self, text: str) -> np.ndarray:
        """テキスト→平均プーリング済み1ベクトル (latent_size,)."""
        z_seq = self.encode(text)
        return z_seq.mean(axis=0)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def cosine_sim_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """コサイン類似度行列 (n, m)."""
    A_norm = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-10)
    B_norm = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-10)
    return A_norm @ B_norm.T


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# データロード
# ---------------------------------------------------------------------------
def load_data(repo_root: Path) -> list[QuestionData]:
    """HA20の全データを統合ロード."""
    # 1. core_propositions
    qs_path = repo_root / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
    qs_map = {r["id"]: r for r in load_jsonl(qs_path)}

    # 2. responses
    phase_c = {r["id"]: r for r in load_jsonl(
        repo_root / "data" / "phase_c_scored_v1_t0_only.jsonl"
    )}

    # 3. human annotations
    ha_path = repo_root / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
    human_scores: dict[str, float] = {}
    with open(ha_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            human_scores[row["id"]] = float(row["human_score"])

    # 4. audit results (tfidf hit/miss, f1-f4, S)
    audit_path = repo_root / "data" / "eval" / "audit_102_results.csv"
    audit_map: dict[str, dict] = {}
    with open(audit_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            audit_map[row["id"]] = row

    # 統合
    data: list[QuestionData] = []
    for qid in HA20_IDS:
        qs = qs_map.get(qid, {})
        pc = phase_c.get(qid, {})
        ar = audit_map.get(qid)
        hs = human_scores.get(qid)

        if ar is None or hs is None:
            print(f"[WARN] {qid}: audit or human_score missing, skipping")
            continue

        core_props = qs.get("core_propositions", [])
        # phase_c にも meta_original_core_propositions がある場合はそちらを優先
        if "meta_original_core_propositions" in pc:
            raw = pc["meta_original_core_propositions"]
            if isinstance(raw, str):
                try:
                    core_props = json.loads(raw)
                except json.JSONDecodeError:
                    pass
            elif isinstance(raw, list):
                core_props = raw

        data.append(QuestionData(
            question_id=qid,
            human_score=hs,
            response=pc.get("response", ""),
            core_propositions=core_props,
            hit_ids=json.loads(ar["hit_ids"]),
            miss_ids=json.loads(ar["miss_ids"]),
            S=float(ar["S"]),
            f1=float(ar["f1"]),
            f2=float(ar["f2"]),
            f3=float(ar["f3"]),
            f4=float(ar["f4"]),
        ))

    return data


# ---------------------------------------------------------------------------
# z-gate スコアリング
# ---------------------------------------------------------------------------
def compute_z_scores(
    data: list[QuestionData], calm: CALMEncoder
) -> list[MissEntry]:
    """tfidf miss 全件に対してz-gateスコアを計算."""
    misses: list[MissEntry] = []

    for q in data:
        if not q.miss_ids:
            continue

        # response を z 空間にエンコード
        resp_z = calm.encode(q.response)  # (num_patches, 128)

        for pidx in q.miss_ids:
            if pidx >= len(q.core_propositions):
                print(f"[WARN] {q.question_id}[{pidx}]: prop index out of range")
                continue

            prop_text = q.core_propositions[pidx]
            prop_z = calm.encode_pooled(prop_text)  # (128,)

            # max cosine similarity across response patches
            sims = cosine_sim_matrix(resp_z, prop_z.reshape(1, -1))  # (N, 1)
            z_score = float(sims.max())

            misses.append(MissEntry(
                question_id=q.question_id,
                prop_idx=pidx,
                proposition=prop_text,
                human_score=q.human_score,
                structural_fail=q.structural_fail,
                S=q.S,
                z_score=z_score,
            ))

    return misses


# ---------------------------------------------------------------------------
# 条件付きマージ + ΔE 再計算
# ---------------------------------------------------------------------------
def run_merge(
    data: list[QuestionData],
    misses: list[MissEntry],
    theta: float,
) -> dict:
    """条件付きマージを適用し、merged ΔE と Spearman ρ を算出."""
    # miss をインデックス化
    miss_map: dict[tuple[str, int], MissEntry] = {
        (m.question_id, m.prop_idx): m for m in misses
    }

    results_per_q: list[dict] = []
    rescued_details: list[dict] = []
    rejected_details: list[dict] = []
    miss_details: list[dict] = []

    for q in data:
        total = len(q.core_propositions)
        tfidf_hits = len(q.hit_ids)
        z_rescued = 0

        for pidx in q.miss_ids:
            m = miss_map.get((q.question_id, pidx))
            if m is None:
                continue

            detail = {
                "id": q.question_id,
                "prop_idx": pidx,
                "proposition": m.proposition[:40],
                "z_score": m.z_score,
                "structural_fail": m.structural_fail,
                "human_score": q.human_score,
            }

            if m.z_score >= theta and not m.structural_fail:
                z_rescued += 1
                detail["verdict"] = "rescued"
                rescued_details.append(detail)
            elif m.z_score >= theta and m.structural_fail:
                detail["verdict"] = "rejected(gate_fail)"
                rejected_details.append(detail)
            else:
                detail["verdict"] = f"miss(z={m.z_score:.3f}<θ)"
                miss_details.append(detail)

        merged_hits = tfidf_hits + z_rescued
        C = merged_hits / total if total > 0 else 0.0
        S = q.S
        # ΔE = (2*(1-S)^2 + 1*(1-C)^2) / 3
        dE = (2 * (1 - S) ** 2 + 1 * (1 - C) ** 2) / 3

        results_per_q.append({
            "id": q.question_id,
            "human_score": q.human_score,
            "tfidf_hits": tfidf_hits,
            "z_rescued": z_rescued,
            "merged_hits": merged_hits,
            "total": total,
            "C": C,
            "S": S,
            "dE": dE,
            "structural_fail": q.structural_fail,
        })

    # Spearman ρ
    human_scores = [r["human_score"] for r in results_per_q]
    merged_dE = [r["dE"] for r in results_per_q]
    rho, pval = spearmanr(merged_dE, human_scores)

    # tfidf-only ΔE (参考)
    tfidf_dE = []
    for q in data:
        total = len(q.core_propositions)
        C_orig = len(q.hit_ids) / total if total > 0 else 0.0
        dE_orig = (2 * (1 - q.S) ** 2 + 1 * (1 - C_orig) ** 2) / 3
        tfidf_dE.append(dE_orig)
    rho_tfidf, pval_tfidf = spearmanr(tfidf_dE, human_scores)

    # 集計
    total_misses = sum(len(q.miss_ids) for q in data)
    total_props = sum(len(q.core_propositions) for q in data)
    total_tfidf_hits = sum(len(q.hit_ids) for q in data)
    total_rescued = sum(r["z_rescued"] for r in results_per_q)
    total_merged = total_tfidf_hits + total_rescued

    # 偽陽性チェック: rescued かつ human_score <= 2
    false_positives = [d for d in rescued_details if d["human_score"] <= 2]

    return {
        "theta": theta,
        "results_per_q": results_per_q,
        "rescued_details": rescued_details,
        "rejected_details": rejected_details,
        "miss_details": miss_details,
        "total_props": total_props,
        "total_tfidf_hits": total_tfidf_hits,
        "total_rescued": total_rescued,
        "total_merged": total_merged,
        "total_misses": total_misses,
        "rho": rho,
        "pval": pval,
        "rho_tfidf": rho_tfidf,
        "pval_tfidf": pval_tfidf,
        "false_positives": false_positives,
    }


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def print_results(res: dict) -> None:
    theta = res["theta"]
    print(f"\n{'='*60}")
    print(f"  θ = {theta:.2f}")
    print(f"{'='*60}")

    print(f"\n--- tfidf miss {res['total_misses']}件のz-gateスコア ---\n")

    all_details = (
        res["rescued_details"] + res["rejected_details"] + res["miss_details"]
    )
    all_details.sort(key=lambda d: (d["id"], d["prop_idx"]))

    for d in all_details:
        print(f"  {d['id']}[{d['prop_idx']}]: z={d['z_score']:.4f} "
              f"→ {d['verdict']}"
              f"  (hs={d['human_score']:.0f})")

    print("\n--- Merged hit率 ---\n")
    print(f"  {res['total_merged']}/{res['total_props']} "
          f"({res['total_merged']/res['total_props']*100:.1f}%)  "
          f"[tfidf: {res['total_tfidf_hits']}/{res['total_props']}]")
    print(f"  tfidf_hit: {res['total_tfidf_hits']}, "
          f"z_rescued: {res['total_rescued']}, "
          f"miss: {res['total_misses'] - res['total_rescued']}")

    print("\n--- Merged ΔE vs human_score ---\n")
    print(f"  Spearman ρ = {res['rho']:.4f} (p={res['pval']:.4f})")
    print(f"  [参考] tfidf-only ρ = {res['rho_tfidf']:.4f} "
          f"(p={res['pval_tfidf']:.4f})")

    print("\n--- 偽陽性チェック ---\n")
    n_fp = len(res["false_positives"])
    print(f"  z_rescued × human_score<=2: {n_fp}件")
    for fp in res["false_positives"]:
        print(f"    {fp['id']}[{fp['prop_idx']}]: z={fp['z_score']:.4f}, "
              f"hs={fp['human_score']:.0f}, prop='{fp['proposition']}'")

    print("\n--- 件別結果 ---\n")
    print(f"  {'id':<6} {'hs':>3} {'tfidf':>6} {'z_res':>6} {'merged':>7} "
          f"{'C':>5} {'S':>5} {'ΔE':>6} {'gate':>5}")
    print(f"  {'-'*55}")
    for r in res["results_per_q"]:
        gate = "FAIL" if r["structural_fail"] else "pass"
        print(f"  {r['id']:<6} {r['human_score']:>3.0f} "
              f"{r['tfidf_hits']:>3}/{r['total']:<2} "
              f"{r['z_rescued']:>3}/{r['total'] - r['tfidf_hits']:<2} "
              f"{r['merged_hits']:>3}/{r['total']:<2} "
              f"{r['C']:>5.3f} {r['S']:>5.3f} {r['dE']:>6.4f} {gate:>5}")


# ---------------------------------------------------------------------------
# 判定
# ---------------------------------------------------------------------------
def print_verdict(results_by_theta: list[dict]) -> None:
    print(f"\n{'='*60}")
    print("  受け入れ条件の判定")
    print(f"{'='*60}\n")

    for res in results_by_theta:
        theta = res["theta"]
        rho_pass = abs(res["rho"]) >= 0.85
        hit_pass = res["total_merged"] > res["total_tfidf_hits"]
        fp_pass = len(res["false_positives"]) == 0

        print(f"  θ={theta:.2f}:")
        print(f"    1. ρ >= 0.85: {'PASS' if rho_pass else 'FAIL'} "
              f"(ρ={res['rho']:.4f})")
        print(f"    2. ヒット率純増: {'PASS' if hit_pass else 'FAIL'} "
              f"({res['total_tfidf_hits']} → {res['total_merged']})")
        print(f"    3. 偽陽性0: {'PASS' if fp_pass else 'FAIL'} "
              f"({len(res['false_positives'])}件)")
        all_pass = rho_pass and hit_pass and fp_pass
        print(f"    → 総合: {'ALL PASS' if all_pass else 'FAIL'}")
        print()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="z-gate 条件付きマージ検証")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    print(f"[INFO] repo_root: {repo_root}")

    # データロード
    data = load_data(repo_root)
    print(f"[INFO] HA20: {len(data)} questions loaded")

    total_hits = sum(len(q.hit_ids) for q in data)
    total_misses = sum(len(q.miss_ids) for q in data)
    print(f"[INFO] tfidf: {total_hits} hits, {total_misses} misses")

    gate_fail_qs = [q for q in data if q.structural_fail]
    gate_fail_misses = sum(len(q.miss_ids) for q in gate_fail_qs)
    print(f"[INFO] 構造ゲートfail: {len(gate_fail_qs)}件, "
          f"miss中の却下対象: {gate_fail_misses}件")
    print(f"[INFO] z-gate救済候補: {total_misses - gate_fail_misses}件")

    # CALM エンコーダ初期化
    t0 = time.time()
    calm = CALMEncoder(device=args.device)
    print(f"[INFO] CALM loaded in {time.time() - t0:.1f}s")

    # z-gate スコアリング
    t0 = time.time()
    misses = compute_z_scores(data, calm)
    print(f"[INFO] z-gate scoring done in {time.time() - t0:.1f}s "
          f"({len(misses)} misses scored)")

    # 再現性チェック: 同一入力で再スコア
    if misses:
        m0 = misses[0]
        resp_z = calm.encode(
            next(q.response for q in data if q.question_id == m0.question_id)
        )
        prop_z = calm.encode_pooled(m0.proposition)
        sims = cosine_sim_matrix(resp_z, prop_z.reshape(1, -1))
        z_check = float(sims.max())
        assert abs(z_check - m0.z_score) < 1e-6, (
            f"再現性チェック失敗: {z_check} != {m0.z_score}"
        )
        print("[INFO] 再現性チェック: PASS")

    # 感度分析: 3閾値
    print(f"\n{'='*60}")
    print("  z-gate 条件付きマージ検証結果")
    print(f"{'='*60}")

    results_by_theta: list[dict] = []
    for theta in THETAS:
        res = run_merge(data, misses, theta)
        print_results(res)
        results_by_theta.append(res)

    # 総合判定
    print_verdict(results_by_theta)

    # 全miss z_score 一覧（記録用）
    print(f"\n--- 全{len(misses)}件 z_score 一覧 ---\n")
    misses_sorted = sorted(misses, key=lambda m: m.z_score, reverse=True)
    for m in misses_sorted:
        gate = "FAIL" if m.structural_fail else "pass"
        print(f"  {m.question_id}[{m.prop_idx}]: z={m.z_score:.4f}  "
              f"gate={gate}  hs={m.human_score:.0f}  "
              f"prop='{m.proposition[:50]}'")


if __name__ == "__main__":
    main()
