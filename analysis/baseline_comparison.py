"""analysis/baseline_comparison.py — UGHer vs established baselines (HA48/HA20)

paper review item #5 (2026-04-21 session で defer) への直接対応。HA48 / HA20 の
(response, reference, human O) トリプルに対し以下の baseline を算出し、UGHer
(ΔE) との Spearman ρ 比較を行う:

- **BLEU (sacrebleu)** — 古典的な lexical overlap (n-gram), char tokenization
  で日本語対応
- **BERTScore (xlm-roberta-base)** — 多言語 contextual embedding ベースの F1
- **SBert cos (paraphrase-multilingual-MiniLM-L12-v2)** — semantic embedding
  cosine 類似度、既存 cascade モデル再利用で install 追加ゼロ
- **UGHer ΔE** — 本プロジェクト指標 (1 - ΔE を similarity として比較)

BLEURT は TF 依存 + 専用 checkpoint が重く本 PR では scope 外。BERTScore が
contextual embedding baseline を担当する。

出力:
  - analysis/baseline_comparison_ha48.csv (per-qid metric + O)
  - analysis/baseline_comparison_ha20.csv (同上)
  - analysis/baseline_comparison_summary.md (ρ / CI / 対比表)

使い方:
  pip install bert-score sacrebleu
  python analysis/baseline_comparison.py

再現性のため全 metric を 1 script で算出。計算時間目安 (CPU): HA48 で ~5 分
(BERTScore の XLM-R load が dominant)。
"""
from __future__ import annotations

import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Data paths
HA48_CSV = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
HA20_CSV = ROOT / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
PHASE_C_JSONL = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"
Q_META_JSONL = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.jsonl"

# Output paths
OUT_CSV_HA48 = ROOT / "analysis" / "baseline_comparison_ha48.csv"
OUT_CSV_HA20 = ROOT / "analysis" / "baseline_comparison_ha20.csv"
OUT_MD = ROOT / "analysis" / "baseline_comparison_summary.md"

# Model choices (multilingual, installed via bert-score / cascade_matcher)
BERTSCORE_MODEL = "xlm-roberta-base"
BERTSCORE_LANG = "ja"
SBERT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Statistical constants
Z_CRIT_95 = 1.959963984540054


@dataclass(frozen=True)
class Pair:
    qid: str
    response: str
    reference: str
    o: float  # human score (1-5)


# --- Data loading ---


def _load_phase_c_responses() -> Dict[str, dict]:
    """Load phase_c_scored JSONL as {id: {response, reference, delta_e_full, ...}}."""
    result: Dict[str, dict] = {}
    with open(PHASE_C_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("id")
            if qid:
                result[qid] = rec
    return result


def _load_ha48_o_scores() -> Dict[str, float]:
    result: Dict[str, float] = {}
    with open(HA48_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                result[row["id"]] = float(row["O"])
            except (KeyError, ValueError):
                continue
    return result


def _load_ha20_o_scores() -> Dict[str, float]:
    """HA20 の human_score を O 相当として返す (1-5 Likert, HA48 と同スケール)."""
    result: Dict[str, float] = {}
    with open(HA20_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                result[row["id"]] = float(row["human_score"])
            except (KeyError, ValueError):
                continue
    return result


def _build_pairs(
    o_scores: Dict[str, float], phase_c: Dict[str, dict],
) -> List[Pair]:
    pairs: List[Pair] = []
    for qid, o in o_scores.items():
        rec = phase_c.get(qid)
        if not rec:
            continue
        response = rec.get("response", "") or ""
        reference = rec.get("reference", "") or ""
        if not response or not reference:
            continue
        pairs.append(Pair(qid=qid, response=response, reference=reference, o=o))
    return pairs


# --- Metric calculators ---


def compute_bleu_scores(pairs: List[Pair]) -> List[float]:
    """BLEU (sacrebleu) per pair。日本語は `tokenize='char'` で安定 tokenize."""
    from sacrebleu.metrics import BLEU

    bleu = BLEU(tokenize="char", effective_order=True)
    scores: List[float] = []
    for p in pairs:
        # sentence_score は 0-100 スケール。0-1 に正規化
        s = bleu.sentence_score(p.response, [p.reference]).score / 100.0
        scores.append(s)
    return scores


def compute_bertscores(pairs: List[Pair]) -> List[float]:
    """BERTScore F1 per pair (多言語モデル `xlm-roberta-base`, lang='ja')。"""
    from bert_score import score as bertscore

    cands = [p.response for p in pairs]
    refs = [p.reference for p in pairs]
    # idf=False: reference 数が少ない (48) ので idf weighting は不安定
    # rescale_with_baseline=False: 多言語 xlm-r のベースラインファイルがないため
    P, R, F1 = bertscore(
        cands, refs,
        model_type=BERTSCORE_MODEL,
        lang=BERTSCORE_LANG,
        idf=False,
        rescale_with_baseline=False,
        verbose=False,
    )
    return [float(f) for f in F1.tolist()]


def compute_sbert_cosines(pairs: List[Pair]) -> List[float]:
    """SBert cosine similarity per pair (既存 cascade_matcher モデル再利用)。"""
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(SBERT_MODEL)
    cands = [p.response for p in pairs]
    refs = [p.reference for p in pairs]
    emb_c = model.encode(cands, convert_to_numpy=True, normalize_embeddings=True)
    emb_r = model.encode(refs, convert_to_numpy=True, normalize_embeddings=True)
    # normalized vectors → dot = cosine
    sims = np.einsum("ij,ij->i", emb_c, emb_r)
    return [float(s) for s in sims]


def compute_ugher_similarities(pairs: List[Pair], q_meta: Dict[str, dict]) -> List[float]:
    """UGHer: 1 - ΔE を similarity として返す (他 metric と向きを揃える)。

    **current pipeline** (`detector.detect` → `ugh_calculator.calculate`) で
    算出した ΔE を使用。`docs/validation.md` 報告値 (HA48 ρ=-0.4817, HA20
    ρ=-0.7737, いずれも system C 基準) と同じ計算パスで、論文主張値と
    apples-to-apples の比較を実現する。

    phase_c JSONL の `delta_e_full` は phase_c 時代の別定義 (reference 全文と
    の直接 SBert 距離) であり、**ここでは使わない**。

    q_meta に該当 id がない / detect 失敗時は NaN。
    """
    from detector import detect
    from ugh_calculator import calculate

    result: List[float] = []
    for p in pairs:
        meta = q_meta.get(p.qid)
        if not meta or not meta.get("core_propositions"):
            result.append(float("nan"))
            continue
        try:
            ev = detect(p.qid, p.response, meta)
            state = calculate(ev)
            if state.delta_e is None:
                result.append(float("nan"))
            else:
                result.append(1.0 - float(state.delta_e))
        except Exception:
            result.append(float("nan"))
    return result


def _load_q_meta() -> Dict[str, dict]:
    """question_sets JSONL を {id: meta} で返す (core_propositions 等)."""
    result: Dict[str, dict] = {}
    with open(Q_META_JSONL, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("id")
            if qid:
                result[qid] = rec
    return result


# --- Correlation analysis ---


def fisher_ci(rho: float, n: int, alpha: float = 0.05) -> Tuple[float, float]:
    """Fisher z 変換 95% CI (docs/validation.md と同式)。"""
    z = math.atanh(rho)
    se = 1.0 / math.sqrt(n - 3)
    lo = math.tanh(z - Z_CRIT_95 * se)
    hi = math.tanh(z + Z_CRIT_95 * se)
    return lo, hi


def _spearman(a: List[float], b: List[float]) -> Tuple[float, float]:
    from scipy.stats import spearmanr

    rho, p = spearmanr(a, b)
    return float(rho), float(p)


def _filter_finite(
    xs: List[float], ys: List[float],
) -> Tuple[List[float], List[float]]:
    pairs = [(x, y) for x, y in zip(xs, ys) if not (math.isnan(x) or math.isnan(y))]
    if not pairs:
        return [], []
    xa, ya = zip(*pairs)
    return list(xa), list(ya)


# --- CSV / Markdown output ---


def write_per_qid_csv(
    path: Path, pairs: List[Pair], metrics: Dict[str, List[float]],
) -> None:
    fieldnames = ["id", "O"] + list(metrics.keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, p in enumerate(pairs):
            row = {"id": p.qid, "O": round(p.o, 4)}
            for name, vals in metrics.items():
                v = vals[i] if i < len(vals) else float("nan")
                row[name] = "" if math.isnan(v) else round(v, 4)
            w.writerow(row)


def _summarize(
    dataset: str, pairs: List[Pair], metrics: Dict[str, List[float]],
) -> dict:
    o = [p.o for p in pairs]
    result = {"dataset": dataset, "n": len(pairs), "rows": []}
    # 指標ごとに O との Spearman + CI
    # 向き: O が高い = 良回答、UGHer/BERTScore/SBert は高 sim = 良、BLEU は高 = 良
    # → 全て正相関を期待する (ΔE のみ「ΔE↓ = 良」だったが UGHer は 1-ΔE に変換済み)
    for name, vals in metrics.items():
        ox, mx = _filter_finite(o, vals)
        if len(ox) < 4:
            result["rows"].append({
                "metric": name, "n_valid": len(ox),
                "rho": None, "p": None, "ci_lo": None, "ci_hi": None,
            })
            continue
        rho, pval = _spearman(mx, ox)
        lo, hi = fisher_ci(rho, len(ox))
        result["rows"].append({
            "metric": name, "n_valid": len(ox),
            "rho": rho, "p": pval, "ci_lo": lo, "ci_hi": hi,
        })
    return result


def write_summary_md(summaries: List[dict]) -> None:
    lines: List[str] = []
    lines.append("# Baseline Comparison — UGHer vs BLEU / BERTScore / SBert")
    lines.append("")
    lines.append("HA48 / HA20 の (response, reference, human O) トリプルに対し、")
    lines.append("UGHer (ΔE ベース) と 3 baseline 指標の Spearman ρ(metric, O) を比較。")
    lines.append("")
    lines.append("**向きの統一:**")
    lines.append("- BLEU / BERTScore / SBert cos: 高 similarity = 良回答 → **正相関期待**")
    lines.append("- UGHer は `1 - ΔE_full` (similarity 向き) で比較 → **正相関期待**")
    lines.append("")
    lines.append("**計算式 (CI):** Fisher z — `tanh(atanh(ρ) ± 1.96/sqrt(n-3))`")
    lines.append("")

    for s in summaries:
        lines.append(f"## {s['dataset']} (n={s['n']})")
        lines.append("")
        lines.append("| 指標 | n_valid | Spearman ρ | p | 95% CI |")
        lines.append("|---|---|---|---|---|")
        for row in s["rows"]:
            if row["rho"] is None:
                lines.append(f"| {row['metric']} | {row['n_valid']} | — | — | — |")
            else:
                ci = f"[{row['ci_lo']:+.4f}, {row['ci_hi']:+.4f}]"
                lines.append(
                    f"| {row['metric']} | {row['n_valid']} | "
                    f"{row['rho']:+.4f} | {row['p']:.4f} | {ci} |"
                )
        lines.append("")

    lines.append("## 解釈")
    lines.append("")
    lines.append("- 本表は「UGHer が既存 baseline に対してどの程度強い信号を示すか」の")
    lines.append("  直接比較を提供する。CI overlap が大きければ 3 指標は統計的に区別")
    lines.append("  困難、overlap が小さければ UGHer の優位性が検証される。")
    lines.append("- 現状 `docs/validation.md §Limitations` に記載した「ベースライン比較の")
    lines.append("  不在」を本 script で埋める。n=48 / n=20 の CI 幅は広いため、結論は")
    lines.append("  「点推定の順位 + CI overlap の定量開示」に留める。")
    lines.append("")
    lines.append("## 再現")
    lines.append("")
    lines.append("```")
    lines.append("pip install bert-score sacrebleu")
    lines.append("python analysis/baseline_comparison.py")
    lines.append("```")
    lines.append("")
    lines.append("出力: `baseline_comparison_ha{48,20}.csv`, `baseline_comparison_summary.md`")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


# --- Main ---


def run_dataset(
    name: str,
    pairs: List[Pair],
    q_meta: Dict[str, dict],
    out_csv: Path,
) -> dict:
    print(f"\n=== {name} (n={len(pairs)}) ===")
    print("  BLEU ...", flush=True)
    bleu = compute_bleu_scores(pairs)
    print("  SBert cos ...", flush=True)
    sbert = compute_sbert_cosines(pairs)
    print("  UGHer (current pipeline, 1 - ΔE) ...", flush=True)
    ugher = compute_ugher_similarities(pairs, q_meta)
    print("  BERTScore (xlm-roberta-base) ...", flush=True)
    bert = compute_bertscores(pairs)

    metrics = {
        "BLEU": bleu,
        "BERTScore_F1": bert,
        "SBert_cos": sbert,
        "UGHer_1mdE": ugher,
    }
    write_per_qid_csv(out_csv, pairs, metrics)
    print(f"  → {out_csv}")
    return _summarize(name, pairs, metrics)


def main() -> None:
    print("Loading data ...")
    phase_c = _load_phase_c_responses()
    q_meta = _load_q_meta()
    print(f"  phase_c entries: {len(phase_c)}")
    print(f"  q_meta entries: {len(q_meta)}")

    ha48_o = _load_ha48_o_scores()
    ha20_o = _load_ha20_o_scores()
    ha48_pairs = _build_pairs(ha48_o, phase_c)
    ha20_pairs = _build_pairs(ha20_o, phase_c)
    print(f"  HA48 pairs: {len(ha48_pairs)} (O-score 件数: {len(ha48_o)})")
    print(f"  HA20 pairs: {len(ha20_pairs)} (human_score 件数: {len(ha20_o)})")

    summaries = []
    summaries.append(run_dataset("HA20", ha20_pairs, q_meta, OUT_CSV_HA20))
    summaries.append(run_dataset("HA48", ha48_pairs, q_meta, OUT_CSV_HA48))

    write_summary_md(summaries)
    print(f"\n=== Summary written to {OUT_MD} ===\n")

    # print quick table to stdout
    for s in summaries:
        print(f"{s['dataset']} (n={s['n']}):")
        for row in s["rows"]:
            if row["rho"] is None:
                print(f"  {row['metric']:<16} ρ=—")
            else:
                ci = f"[{row['ci_lo']:+.4f}, {row['ci_hi']:+.4f}]"
                print(
                    f"  {row['metric']:<16} ρ={row['rho']:+.4f} "
                    f"p={row['p']:.4f} n_valid={row['n_valid']} CI={ci}"
                )


if __name__ == "__main__":
    main()
