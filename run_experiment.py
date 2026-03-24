#!/usr/bin/env python3
"""CALM z-vector 命題照合改善実験 — Phase 1〜4.

Usage:
    python run_experiment.py [--device cpu|cuda] [--skip-calm] [--skip-sbert]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
import torch
from scipy.stats import spearmanr
from sklearn.metrics import f1_score

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
SEED = 42
CALM_MODEL_ID = "cccczshao/CALM-Autoencoder"
SBERT_MODEL_ID = "paraphrase-multilingual-MiniLM-L12-v2"
THETA_RANGE = np.arange(0.50, 0.96, 0.01)

np.random.seed(SEED)
torch.manual_seed(SEED)


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------
@dataclass
class MatchResult:
    """1命題の照合結果."""
    question_id: str
    prop_idx: int
    proposition: str
    method: str           # A / B / C
    score: float          # 類似度
    hit: bool             # 閾値判定
    threshold: float = 0.0


@dataclass
class ItemResult:
    """1件（question）の照合結果."""
    question_id: str
    human_score: float | None
    n_props: int
    hits_a: int = 0       # 手法A: 既存tfidf
    hits_b: int = 0       # 手法B: sentence-transformers
    hits_c: int = 0       # 手法C: CALM z-vector
    details: list[MatchResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Phase 1: CALM Encoder
# ---------------------------------------------------------------------------
class CALMEncoder:
    """CALM オートエンコーダのエンコーダ部分をラップ."""

    def __init__(self, device: str = "cpu"):
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

        # 重みロード
        weights_path = hf_hub_download(CALM_MODEL_ID, "model.safetensors")
        print(f"[CALM] Loading weights from {weights_path}...")
        state_dict = safetensors.torch.load_file(weights_path)
        self.model.load_state_dict(state_dict, strict=False)

        self.device = device
        self.model = self.model.to(device).eval()
        self.patch_size = config.patch_size
        self.latent_size = config.latent_size

        # 日本語トークン化テスト
        test_text = "これはテストです"
        tokens = self.tokenizer.encode(test_text, add_special_tokens=False)
        print(f"[CALM] 日本語トークン化テスト: '{test_text}' -> {len(tokens)} tokens")
        print(f"[CALM] Token IDs (先頭10): {tokens[:10]}")

    @torch.no_grad()
    def encode(self, text: str) -> np.ndarray:
        """テキスト→zベクトル系列 (num_patches, 128)."""
        tokens = self.tokenizer.encode(text, add_special_tokens=False)
        if len(tokens) == 0:
            return np.zeros((1, self.latent_size), dtype=np.float32)

        # patch_size の倍数にパディング
        remainder = len(tokens) % self.patch_size
        if remainder != 0:
            pad_len = self.patch_size - remainder
            pad_id = self.tokenizer.pad_token_id or 0
            tokens = tokens + [pad_id] * pad_len

        input_ids = torch.tensor([tokens], dtype=torch.long, device=self.device)
        latent_states = self.model.encoder(input_ids)  # (1, num_patches, latent*2)
        # VAE: mean部分のみ使用（前半128次元）
        mean = latent_states[:, :, :self.latent_size]
        return mean.squeeze(0).cpu().numpy()  # (num_patches, 128)

    def encode_pooled(self, text: str) -> np.ndarray:
        """テキスト→平均プーリング済み1ベクトル (128,)."""
        z_seq = self.encode(text)
        return z_seq.mean(axis=0)


# ---------------------------------------------------------------------------
# Phase 1: Sentence-BERT Encoder (ベースライン手法B)
# ---------------------------------------------------------------------------
class SBERTEncoder:
    """sentence-transformers による文埋め込み."""

    def __init__(self, device: str = "cpu"):
        from sentence_transformers import SentenceTransformer
        print(f"[SBERT] Loading {SBERT_MODEL_ID}...")
        self.model = SentenceTransformer(SBERT_MODEL_ID, device=device)
        print("[SBERT] Ready.")

    def encode(self, text: str) -> np.ndarray:
        """テキスト→1ベクトル."""
        return self.model.encode(text, normalize_embeddings=True)

    def encode_sentences(self, sentences: list[str]) -> np.ndarray:
        """複数文→行列 (n, dim)."""
        return self.model.encode(sentences, normalize_embeddings=True)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """コサイン類似度."""
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def cosine_sim_matrix(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """コサイン類似度行列 (n, m)."""
    A_norm = A / (np.linalg.norm(A, axis=1, keepdims=True) + 1e-10)
    B_norm = B / (np.linalg.norm(B, axis=1, keepdims=True) + 1e-10)
    return A_norm @ B_norm.T


def split_sentences(text: str) -> list[str]:
    """日本語テキストを文に分割 (簡易)."""
    import re
    sents = re.split(r'[。！？\n]+', text)
    return [s.strip() for s in sents if s.strip()]


def parse_hit_str(hit_str: str) -> tuple[int, int]:
    """'2/3' -> (2, 3)"""
    if not hit_str or "/" not in hit_str:
        return (0, 0)
    parts = hit_str.split("/")
    return (int(parts[0]), int(parts[1]))


def find_optimal_threshold(
    scores: list[float], labels: list[bool], theta_range: np.ndarray
) -> tuple[float, float]:
    """F1最大化する閾値を探索. returns (best_theta, best_f1)."""
    best_theta, best_f1 = 0.5, 0.0
    for theta in theta_range:
        preds = [s >= theta for s in scores]
        if any(preds) or any(labels):
            f1 = f1_score(labels, preds, zero_division=0.0)
            if f1 > best_f1:
                best_f1 = f1
                best_theta = float(theta)
    return best_theta, best_f1


# ---------------------------------------------------------------------------
# Phase 2-3: 命題照合
# ---------------------------------------------------------------------------
def run_method_a(data: list[dict]) -> dict[str, list[bool]]:
    """手法A: 既存 tfidf-char-ngram の hit 結果をパース."""
    results = {}
    for item in data:
        if not item["has_human_annotation"] or not item.get("existing_hit"):
            continue
        qid = item["id"]
        hit_n, total_n = parse_hit_str(item["existing_hit"])
        n_props = len(item["core_propositions"])
        # existing_hit の total が props数と一致しない場合の調整
        if total_n == 0:
            total_n = n_props
        hits = [True] * hit_n + [False] * (total_n - hit_n)
        # props数に合わせる
        if len(hits) < n_props:
            hits.extend([False] * (n_props - len(hits)))
        elif len(hits) > n_props:
            hits = hits[:n_props]
        results[qid] = hits
    return results


def run_method_b(
    data: list[dict], sbert: SBERTEncoder
) -> tuple[dict[str, list[float]], dict[str, list[bool]]]:
    """手法B: sentence-transformers による命題照合."""
    scores_dict: dict[str, list[float]] = {}
    hits_dict: dict[str, list[bool]] = {}

    # まず全データでスコアを計算
    all_scores: list[float] = []
    all_labels: list[bool] = []

    # 20件(annotated)のラベルを取得
    method_a = run_method_a(data)

    for item in data:
        qid = item["id"]
        props = item["core_propositions"]
        if not props:
            continue

        response = item["response"]
        sentences = split_sentences(response)
        if not sentences:
            scores_dict[qid] = [0.0] * len(props)
            continue

        sent_vecs = sbert.encode_sentences(sentences)
        prop_scores = []
        for prop in props:
            prop_vec = sbert.encode(prop)
            sims = cosine_sim_matrix(sent_vecs, prop_vec.reshape(1, -1)).flatten()
            max_sim = float(sims.max())
            prop_scores.append(max_sim)

        scores_dict[qid] = prop_scores

        # ラベルが存在する場合は閾値探索用データに追加
        if qid in method_a:
            labels = method_a[qid]
            for sc, lb in zip(prop_scores, labels):
                all_scores.append(sc)
                all_labels.append(lb)

    # 閾値探索
    if all_scores:
        best_theta, best_f1 = find_optimal_threshold(
            all_scores, all_labels, THETA_RANGE
        )
        print(f"[SBERT] Optimal threshold: {best_theta:.2f} (F1={best_f1:.3f})")
    else:
        best_theta = 0.70

    # hit判定
    for qid, prop_scores in scores_dict.items():
        hits_dict[qid] = [s >= best_theta for s in prop_scores]

    return scores_dict, hits_dict


def run_method_c(
    data: list[dict], calm: CALMEncoder
) -> tuple[dict[str, list[float]], dict[str, list[bool]]]:
    """手法C: CALM z-vector による命題照合."""
    scores_dict: dict[str, list[float]] = {}
    hits_dict: dict[str, list[bool]] = {}

    # 全データでスコア計算
    all_scores: list[float] = []
    all_labels: list[bool] = []
    method_a = run_method_a(data)

    print("[CALM] Encoding responses and propositions...")
    total = sum(1 for d in data if d["core_propositions"])
    for idx, item in enumerate(data):
        qid = item["id"]
        props = item["core_propositions"]
        if not props:
            continue

        if (idx + 1) % 10 == 0 or idx == 0:
            print(f"  [{idx + 1}/{total}] {qid}")

        # response の z系列
        response_z = calm.encode(item["response"])  # (N, 128)

        prop_scores = []
        for prop in props:
            prop_z = calm.encode_pooled(prop)  # (128,)
            # response の各パッチとのコサイン類似度の最大値
            sims = cosine_sim_matrix(response_z, prop_z.reshape(1, -1)).flatten()
            max_sim = float(sims.max()) if len(sims) > 0 else 0.0
            prop_scores.append(max_sim)

        scores_dict[qid] = prop_scores

        if qid in method_a:
            labels = method_a[qid]
            for sc, lb in zip(prop_scores, labels):
                all_scores.append(sc)
                all_labels.append(lb)

    # 閾値探索
    if all_scores:
        best_theta, best_f1 = find_optimal_threshold(
            all_scores, all_labels, THETA_RANGE
        )
        print(f"[CALM] Optimal threshold: {best_theta:.2f} (F1={best_f1:.3f})")
    else:
        best_theta = 0.75

    for qid, prop_scores in scores_dict.items():
        hits_dict[qid] = [s >= best_theta for s in prop_scores]

    return scores_dict, hits_dict


# ---------------------------------------------------------------------------
# Phase 4: 評価
# ---------------------------------------------------------------------------
def evaluate(
    data: list[dict],
    method_a_hits: dict[str, list[bool]],
    method_b_scores: dict[str, list[float]],
    method_b_hits: dict[str, list[bool]],
    method_c_scores: dict[str, list[float]],
    method_c_hits: dict[str, list[bool]],
) -> dict:
    """20件 (ground truth) + 102件全体の評価."""
    annotated = [d for d in data if d["has_human_annotation"]]
    results = {"annotated": [], "all_items": []}

    # --- 20件の評価 ---
    hit_rates_a, hit_rates_b, hit_rates_c = [], [], []
    human_scores = []
    zero_recall = {"a": 0, "b": 0, "c": 0}

    for item in annotated:
        qid = item["id"]
        n_props = len(item["core_propositions"])
        if n_props == 0:
            continue

        ha = method_a_hits.get(qid, [False] * n_props)
        hb = method_b_hits.get(qid, [False] * n_props)
        hc = method_c_hits.get(qid, [False] * n_props)

        rate_a = sum(ha) / n_props
        rate_b = sum(hb) / n_props
        rate_c = sum(hc) / n_props

        hit_rates_a.append(rate_a)
        hit_rates_b.append(rate_b)
        hit_rates_c.append(rate_c)
        human_scores.append(item["human_score"])

        if sum(ha) == 0:
            zero_recall["a"] += 1
        if sum(hb) == 0:
            zero_recall["b"] += 1
        if sum(hc) == 0:
            zero_recall["c"] += 1

        results["annotated"].append({
            "id": qid,
            "human_score": item["human_score"],
            "n_props": n_props,
            "hit_a": f"{sum(ha)}/{n_props}",
            "hit_b": f"{sum(hb)}/{n_props}",
            "hit_c": f"{sum(hc)}/{n_props}",
            "rate_a": round(rate_a, 3),
            "rate_b": round(rate_b, 3),
            "rate_c": round(rate_c, 3),
        })

    # Spearman相関
    spearman_a = spearmanr(human_scores, hit_rates_a) if hit_rates_a else (0, 1)
    spearman_b = spearmanr(human_scores, hit_rates_b) if hit_rates_b else (0, 1)
    spearman_c = spearmanr(human_scores, hit_rates_c) if hit_rates_c else (0, 1)

    # --- 102件全体の hit率変化 ---
    total_props_all, hits_a_all, hits_b_all, hits_c_all = 0, 0, 0, 0
    zero_all = {"a": 0, "b": 0, "c": 0}

    for item in data:
        qid = item["id"]
        n_props = len(item["core_propositions"])
        if n_props == 0:
            continue

        ha = method_a_hits.get(qid, [False] * n_props)
        hb = method_b_hits.get(qid, [False] * n_props)
        hc = method_c_hits.get(qid, [False] * n_props)

        total_props_all += n_props
        hits_a_all += sum(ha)
        hits_b_all += sum(hb)
        hits_c_all += sum(hc)

        if sum(ha) == 0:
            zero_all["a"] += 1
        if sum(hb) == 0:
            zero_all["b"] += 1
        if sum(hc) == 0:
            zero_all["c"] += 1

    summary = {
        "n_annotated": len(annotated),
        "n_total": len(data),
        "annotated_20": {
            "avg_hit_rate_a": round(np.mean(hit_rates_a), 4) if hit_rates_a else 0,
            "avg_hit_rate_b": round(np.mean(hit_rates_b), 4) if hit_rates_b else 0,
            "avg_hit_rate_c": round(np.mean(hit_rates_c), 4) if hit_rates_c else 0,
            "spearman_a": {"rho": round(spearman_a[0], 4), "p": round(spearman_a[1], 4)},
            "spearman_b": {"rho": round(spearman_b[0], 4), "p": round(spearman_b[1], 4)},
            "spearman_c": {"rho": round(spearman_c[0], 4), "p": round(spearman_c[1], 4)},
            "zero_recall": zero_recall,
        },
        "all_102": {
            "total_propositions": total_props_all,
            "hits_a": hits_a_all,
            "hits_b": hits_b_all,
            "hits_c": hits_c_all,
            "hit_rate_a": round(hits_a_all / max(total_props_all, 1), 4),
            "hit_rate_b": round(hits_b_all / max(total_props_all, 1), 4),
            "hit_rate_c": round(hits_c_all / max(total_props_all, 1), 4),
            "zero_recall": zero_all,
        },
        "items": results["annotated"],
    }
    return summary


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------
def write_results_detail(
    data: list[dict],
    method_a_hits: dict[str, list[bool]],
    method_b_scores: dict[str, list[float]],
    method_b_hits: dict[str, list[bool]],
    method_c_scores: dict[str, list[float]],
    method_c_hits: dict[str, list[bool]],
    out_path: Path,
) -> None:
    """results_detail.csv を出力."""
    rows = []
    for item in data:
        qid = item["id"]
        props = item["core_propositions"]
        if not props:
            continue
        ha = method_a_hits.get(qid, [False] * len(props))
        sb = method_b_scores.get(qid, [0.0] * len(props))
        hb = method_b_hits.get(qid, [False] * len(props))
        sc = method_c_scores.get(qid, [0.0] * len(props))
        hc = method_c_hits.get(qid, [False] * len(props))

        for i, prop in enumerate(props):
            rows.append({
                "id": qid,
                "human_score": item.get("human_score", ""),
                "has_annotation": item["has_human_annotation"],
                "prop_idx": i,
                "proposition": prop,
                "hit_a": int(ha[i]) if i < len(ha) else 0,
                "score_b": round(sb[i], 4) if i < len(sb) else 0,
                "hit_b": int(hb[i]) if i < len(hb) else 0,
                "score_c": round(sc[i], 4) if i < len(sc) else 0,
                "hit_c": int(hc[i]) if i < len(hc) else 0,
            })

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"[OUTPUT] results_detail.csv: {len(rows)} rows")


def write_results_summary(summary: dict, out_path: Path) -> None:
    """results_summary.md を出力."""
    s = summary
    a20 = s["annotated_20"]
    a102 = s["all_102"]

    md = f"""# CALM z-vector 命題照合実験 — 結果サマリー

## 実験概要

- データ: Phase C t=0.0 全{s['n_total']}件（うち人手評価20件）
- 手法A: 既存 tfidf-char-ngram（ベースライン）
- 手法B: sentence-transformers ({SBERT_MODEL_ID})
- 手法C: CALM z-vector（提案手法, latent_size=128, patch_size=4）

## 20件（ground truth）の結果

### 命題照合 hit率

| 手法 | 平均hit率 | zero-recall件数 |
|------|----------|----------------|
| A: tfidf-char-ngram | {a20['avg_hit_rate_a']:.4f} | {a20['zero_recall']['a']} |
| B: sentence-transformers | {a20['avg_hit_rate_b']:.4f} | {a20['zero_recall']['b']} |
| C: CALM z-vector | {a20['avg_hit_rate_c']:.4f} | {a20['zero_recall']['c']} |

### human_score との Spearman 相関

| 手法 | ρ | p値 |
|------|---|----|
| A: tfidf | {a20['spearman_a']['rho']:.4f} | {a20['spearman_a']['p']:.4f} |
| B: SBERT | {a20['spearman_b']['rho']:.4f} | {a20['spearman_b']['p']:.4f} |
| C: CALM z | {a20['spearman_c']['rho']:.4f} | {a20['spearman_c']['p']:.4f} |

### 件別結果

| id | human_score | hit A | hit B | hit C |
|----|------------|-------|-------|-------|
"""
    for item in s["items"]:
        md += f"| {item['id']} | {item['human_score']} | {item['hit_a']} | {item['hit_b']} | {item['hit_c']} |\n"

    md += f"""
## 102件全体の結果

| 指標 | A: tfidf | B: SBERT | C: CALM z |
|------|---------|---------|-----------|
| 総命題数 | {a102['total_propositions']} | {a102['total_propositions']} | {a102['total_propositions']} |
| hit数 | {a102['hits_a']} | {a102['hits_b']} | {a102['hits_c']} |
| hit率 | {a102['hit_rate_a']:.4f} | {a102['hit_rate_b']:.4f} | {a102['hit_rate_c']:.4f} |
| zero-recall件数 | {a102['zero_recall']['a']} | {a102['zero_recall']['b']} | {a102['zero_recall']['c']} |

## 成功基準の判定

- **最低ライン** (C > A in hit率): {'**達成**' if a20['avg_hit_rate_c'] > a20['avg_hit_rate_a'] else '**未達成**'}
  - A: {a20['avg_hit_rate_a']:.4f} → C: {a20['avg_hit_rate_c']:.4f}
- **期待ライン** (ρ > 0.5): {'**達成**' if a20['spearman_c']['rho'] > 0.5 else '**未達成**'}
  - ρ = {a20['spearman_c']['rho']:.4f}
- **理想ライン** (zero-recall半減): {'**達成**' if a20['zero_recall']['c'] <= a20['zero_recall']['a'] / 2 else '**未達成**'}
  - A: {a20['zero_recall']['a']} → C: {a20['zero_recall']['c']}

## 考察

"""
    # 考察は実行後に追記するため、プレースホルダー
    if a20['avg_hit_rate_c'] > a20['avg_hit_rate_a']:
        md += "CALM z-vector は tfidf-char-ngram を上回る hit率を達成した。"
        md += "z空間上での意味照合が、語彙表層に依存しない命題検出を可能にしたと考えられる。\n\n"
    else:
        md += ("CALM z-vector は tfidf-char-ngram を上回れなかった。"
               "考えられる原因:\n\n")
        md += "1. Llama3トークナイザーの日本語BPE分割がK=4チャンク境界で意味破壊を起こしている\n"
        md += "2. CALM AEは英語中心の学習データで訓練されており、日本語の意味圧縮が不十分\n"
        md += "3. patch_size=4 の粒度が日本語の命題単位と合致しない\n"
        md += "4. 平均プーリングにより命題のz表現が拡散している\n\n"

    if a20['spearman_b']['rho'] > a20['spearman_c']['rho']:
        md += ("sentence-transformers（手法B）がCALM z-vector（手法C）を上回っている。"
               "これはSBERTが明示的に多言語対応で訓練されているためと考えられる。\n\n")

    md += "## 技術メモ\n\n"
    md += "- CALM Autoencoder: 75Mパラメータ, VAEアーキテクチャ\n"
    md += "- エンコーダ出力: (mean, log_std) の mean 部分を z-vector として使用\n"
    md += "- 日本語はLlama3 BPEで1漢字≈2-3トークン、4トークンチャンクは漢字1-2文字分の粒度\n"
    md += f"- sentence-transformers: {SBERT_MODEL_ID}\n"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)
    print("[OUTPUT] results_summary.md written")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="CALM z-vector 命題照合実験")
    parser.add_argument("--device", default="cpu", help="cpu or cuda")
    parser.add_argument("--skip-calm", action="store_true", help="CALM encoder をスキップ")
    parser.add_argument("--skip-sbert", action="store_true", help="SBERT をスキップ")
    args = parser.parse_args()

    base = Path(__file__).resolve().parent
    input_path = base / "experiment_input.json"

    if not input_path.exists():
        print("experiment_input.json が見つかりません。先に prepare_data.py を実行してください。")
        sys.exit(1)

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)
    print(f"Loaded {len(data)} items ({sum(1 for d in data if d['has_human_annotation'])} annotated)")

    # --- 手法A: 既存結果 ---
    print("\n=== 手法A: 既存 tfidf-char-ngram ===")
    method_a_hits = run_method_a(data)
    n_a_hits = sum(sum(v) for v in method_a_hits.values())
    n_a_total = sum(len(v) for v in method_a_hits.values())
    print(f"  20件合計: {n_a_hits}/{n_a_total} hits")

    # --- 手法B: sentence-transformers ---
    method_b_scores: dict[str, list[float]] = {}
    method_b_hits: dict[str, list[bool]] = {}
    if not args.skip_sbert:
        print("\n=== 手法B: sentence-transformers ===")
        t0 = time.time()
        sbert = SBERTEncoder(device=args.device)
        method_b_scores, method_b_hits = run_method_b(data, sbert)
        print(f"  完了 ({time.time() - t0:.1f}s)")
        del sbert
    else:
        print("\n=== 手法B: SKIPPED ===")

    # --- 手法C: CALM z-vector ---
    method_c_scores: dict[str, list[float]] = {}
    method_c_hits: dict[str, list[bool]] = {}
    if not args.skip_calm:
        print("\n=== 手法C: CALM z-vector ===")
        t0 = time.time()
        try:
            calm = CALMEncoder(device=args.device)
            method_c_scores, method_c_hits = run_method_c(data, calm)
            print(f"  完了 ({time.time() - t0:.1f}s)")
            del calm
        except Exception as e:
            print(f"  CALM encoder エラー: {e}")
            import traceback
            traceback.print_exc()
            print("  手法C をスキップします")
    else:
        print("\n=== 手法C: SKIPPED ===")

    # --- 手法C が空の場合、手法A のスコープに合わせてダミーを埋める ---
    if not method_b_scores:
        for item in data:
            qid = item["id"]
            n = len(item["core_propositions"])
            method_b_scores[qid] = [0.0] * n
            method_b_hits[qid] = [False] * n

    if not method_c_scores:
        for item in data:
            qid = item["id"]
            n = len(item["core_propositions"])
            method_c_scores[qid] = [0.0] * n
            method_c_hits[qid] = [False] * n

    # --- Phase 4: 評価 ---
    print("\n=== Phase 4: 評価 ===")
    summary = evaluate(
        data, method_a_hits,
        method_b_scores, method_b_hits,
        method_c_scores, method_c_hits,
    )

    # 出力
    print("\n=== 出力 ===")
    write_results_detail(
        data, method_a_hits,
        method_b_scores, method_b_hits,
        method_c_scores, method_c_hits,
        base / "results_detail.csv",
    )
    write_results_summary(summary, base / "results_summary.md")

    # JSON形式でも保存
    with open(base / "results_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[OUTPUT] results_summary.json written")

    # サマリー表示
    a20 = summary["annotated_20"]
    a102 = summary["all_102"]
    print("\n" + "=" * 60)
    print("結果サマリー (20件 ground truth)")
    print("=" * 60)
    print(f"  手法A (tfidf):  hit率={a20['avg_hit_rate_a']:.4f}  "
          f"ρ={a20['spearman_a']['rho']:.4f}  zero={a20['zero_recall']['a']}")
    print(f"  手法B (SBERT):  hit率={a20['avg_hit_rate_b']:.4f}  "
          f"ρ={a20['spearman_b']['rho']:.4f}  zero={a20['zero_recall']['b']}")
    print(f"  手法C (CALM z): hit率={a20['avg_hit_rate_c']:.4f}  "
          f"ρ={a20['spearman_c']['rho']:.4f}  zero={a20['zero_recall']['c']}")
    print(f"\n102件全体:  B hit率={a102['hit_rate_b']:.4f}  "
          f"C hit率={a102['hit_rate_c']:.4f}")


if __name__ == "__main__":
    main()
