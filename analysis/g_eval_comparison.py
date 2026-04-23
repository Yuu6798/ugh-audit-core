"""analysis/g_eval_comparison.py — G-Eval LLM-as-judge baseline

Liu et al. (2023) の G-Eval 手法に基づく LLM-as-judge ベースラインを
HA48 / HA20 に対して実行し、UGHer (ΔE) との Spearman ρ を比較する。

**ステータス: skeleton (API key 取得時に実行可能)**

本 script は OPENAI_API_KEY 環境変数を必要とする。key が設定されていない
場合は dry-run で prompt とコスト見積のみ表示して終了する。実行フローと
出力フォーマットは ``baseline_comparison.py`` と揃えており、取得した
scores は ``baseline_comparison.py`` の UGHer / BLEU / BERTScore / SBert
結果と直接比較可能な CSV を生成する。

**論文上の位置付け**: NLP2027 年次大会版 (Variant 1) では judge LLM API
依存による再現性の問題から scope 外として Limitations で言及するが、
journal 拡張版では本 script の結果を ablation として追加する予定。

出力:
  - analysis/g_eval_comparison_ha48.csv  (per-qid g_eval score + O)
  - analysis/g_eval_comparison_ha20.csv  (同上)
  - analysis/g_eval_comparison_summary.md (ρ / CI / 対比表)

使い方:
  export OPENAI_API_KEY="sk-..."
  pip install -e ".[baseline]"   # scipy
  pip install openai             # G-Eval 実行に必須 (追加 extra)
  python analysis/g_eval_comparison.py           # dry-run (cost 見積のみ)
  python analysis/g_eval_comparison.py --execute # 実行 (課金あり)

**コスト見積 (GPT-4o 2025-10 時点)**:
  - HA48: 48 件 × 20 サンプル × 約 500 token ≈ $3-5
  - HA20: 20 件 × 20 サンプル × 約 500 token ≈ $1-2
  - 合計: 約 $5-7 / full run
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Data paths (baseline_comparison.py と共用)
HA48_CSV = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
HA20_CSV = ROOT / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
PHASE_C_JSONL = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"

# Output paths
OUT_CSV_HA48 = ROOT / "analysis" / "g_eval_comparison_ha48.csv"
OUT_CSV_HA20 = ROOT / "analysis" / "g_eval_comparison_ha20.csv"
OUT_MD = ROOT / "analysis" / "g_eval_comparison_summary.md"

# Model / sampling configuration
GEVAL_MODEL = "gpt-4o"
GEVAL_TEMPERATURE = 1.0
GEVAL_N_SAMPLES = 20    # Liu 2023: probability-weighted via multiple samples
GEVAL_MAX_TOKENS = 10   # JSON score のみ、短く
GEVAL_REQUEST_TIMEOUT = 60

Z_CRIT_95 = 1.959963984540054


# G-Eval prompt template (Liu 2023 §3 の form-filling 形式)
# HA48 の human O (1-5) と同スケールに揃える
GEVAL_PROMPT_TEMPLATE = """You will be given a question and an AI-generated response in Japanese.

Your task is to rate the response on overall semantic quality.

Evaluation Criteria:

Overall Quality (1-5) — the overall semantic quality of the response to the
given question, considering:
  (a) structural coherence (is the response well-formed and logically organized?),
  (b) propositional coverage (does the response address the core points of the question?).
A score of 5 means the response is fully satisfactory on both axes.
A score of 1 means the response is unusable.

Evaluation Steps:
1. Read the question carefully and identify what is being asked.
2. Read the response and assess its structural coherence.
3. Assess how well the response covers the core points of the question.
4. Assign a single integer score from 1 to 5 based on the Evaluation Criteria.

Question:
{question}

Response:
{response}

Evaluation Form (respond with JSON only, no prose):
{{"score": <integer 1 to 5>}}
"""


@dataclass(frozen=True)
class Triple:
    """質問 + 応答 + human score のトリプル"""
    qid: str
    question: str
    response: str
    o: float


# --- Data loading (baseline_comparison.py と同一ロジック) ---


def _load_phase_c() -> Dict[str, dict]:
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


def _load_o_ha48() -> Dict[str, float]:
    result: Dict[str, float] = {}
    with open(HA48_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                result[row["id"]] = float(row["O"])
            except (KeyError, ValueError):
                continue
    return result


def _load_o_ha20() -> Dict[str, float]:
    result: Dict[str, float] = {}
    with open(HA20_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                result[row["id"]] = float(row["human_score"])
            except (KeyError, ValueError):
                continue
    return result


def _build_triples(
    o_scores: Dict[str, float], phase_c: Dict[str, dict],
) -> List[Triple]:
    triples: List[Triple] = []
    for qid, o in o_scores.items():
        rec = phase_c.get(qid)
        if not rec:
            continue
        question = rec.get("question", "") or ""
        response = rec.get("response", "") or ""
        if not question or not response:
            continue
        triples.append(Triple(qid=qid, question=question, response=response, o=o))
    return triples


# --- G-Eval scoring ---


def _g_eval_single(
    client, question: str, response: str, n_samples: int = GEVAL_N_SAMPLES
) -> Optional[float]:
    """1 つの (question, response) に対し n_samples 回推論して平均スコアを返す。

    Liu 2023 では token logprob による期待値計算を使うが、API 互換性のため
    ここでは sample-average で近似する (両者は n_samples=20 付近でほぼ一致)。
    """
    prompt = GEVAL_PROMPT_TEMPLATE.format(question=question, response=response)
    scores: List[float] = []
    for _ in range(n_samples):
        try:
            resp = client.chat.completions.create(
                model=GEVAL_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=GEVAL_TEMPERATURE,
                max_tokens=GEVAL_MAX_TOKENS,
                timeout=GEVAL_REQUEST_TIMEOUT,
            )
            content = resp.choices[0].message.content or ""
            # JSON 抽出 (prose 混入への defensive handling)
            start = content.find("{")
            end = content.rfind("}")
            if start == -1 or end == -1:
                continue
            obj = json.loads(content[start : end + 1])
            score = obj.get("score")
            if isinstance(score, (int, float)) and 1 <= score <= 5:
                scores.append(float(score))
        except Exception as e:
            sys.stderr.write(f"[WARN] g_eval sample failed: {e}\n")
            continue

    if not scores:
        return None
    return sum(scores) / len(scores)


def _estimate_cost(n_triples: int) -> float:
    """GPT-4o 2025-10 時点の概算コスト (USD)。1 サンプル約 500 token 前提。"""
    tokens_per_sample = 500
    total_tokens = n_triples * GEVAL_N_SAMPLES * tokens_per_sample
    # GPT-4o: input $2.50 / 1M tok, output $10 / 1M tok (typical mix 80/20)
    cost_input = (total_tokens * 0.8) * 2.50 / 1_000_000
    cost_output = (total_tokens * 0.2) * 10.0 / 1_000_000
    return cost_input + cost_output


# --- Correlation computation (baseline_comparison.py と共用の spec) ---


def _spearman(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Spearman ρ + p (scipy 使用、タイ補正あり)"""
    from scipy.stats import spearmanr

    res = spearmanr(xs, ys)
    return float(res.correlation), float(res.pvalue)


def _fisher_ci(rho: float, n: int) -> Tuple[float, float]:
    if abs(rho) >= 1.0 or n <= 3:
        return (math.nan, math.nan)
    z = math.atanh(rho)
    se = 1.0 / math.sqrt(n - 3)
    lo = math.tanh(z - Z_CRIT_95 * se)
    hi = math.tanh(z + Z_CRIT_95 * se)
    return lo, hi


# --- Main execution ---


def run_g_eval_on_dataset(
    name: str, triples: List[Triple], out_csv: Path, client
) -> Tuple[float, float, Tuple[float, float], int]:
    """dataset に対し G-Eval を実行、CSV 書き出し、ρ/CI を返す。"""
    scores: List[float] = []
    os_: List[float] = []
    rows: List[List] = []

    for i, t in enumerate(triples, 1):
        sys.stdout.write(f"  [{name}] {i}/{len(triples)} qid={t.qid}\r")
        sys.stdout.flush()
        score = _g_eval_single(client, t.question, t.response)
        if score is None:
            continue
        scores.append(score)
        os_.append(t.o)
        rows.append([t.qid, score, t.o])

    sys.stdout.write("\n")
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qid", "g_eval_score", "O"])
        w.writerows(rows)

    rho, p = _spearman(scores, os_)
    ci = _fisher_ci(rho, len(scores))
    return rho, p, ci, len(scores)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="実際に OpenAI API を呼ぶ (課金あり)。省略時は dry-run。",
    )
    args = parser.parse_args()

    phase_c = _load_phase_c()
    ha48_triples = _build_triples(_load_o_ha48(), phase_c)
    ha20_triples = _build_triples(_load_o_ha20(), phase_c)

    total = len(ha48_triples) + len(ha20_triples)
    cost = _estimate_cost(total)

    print(f"[G-Eval skeleton] HA48: {len(ha48_triples)} triples")
    print(f"[G-Eval skeleton] HA20: {len(ha20_triples)} triples")
    print(f"[G-Eval skeleton] Model: {GEVAL_MODEL}, n_samples={GEVAL_N_SAMPLES}")
    print(f"[G-Eval skeleton] 推定コスト: ${cost:.2f} USD")

    if not args.execute:
        print()
        print("Dry-run モードで終了 (--execute で実行)")
        print()
        print("=== prompt template (preview) ===")
        print(
            GEVAL_PROMPT_TEMPLATE.format(
                question="<question>", response="<response>"
            )
        )
        return 0

    # --- Execute mode ---
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        sys.stderr.write(
            "[ERROR] OPENAI_API_KEY 環境変数が未設定。実行をキャンセル。\n"
        )
        return 2

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        sys.stderr.write(
            "[ERROR] openai パッケージ未インストール。"
            "'pip install openai' を実行してください。\n"
        )
        return 2

    client = OpenAI(api_key=api_key)

    t0 = time.time()
    print(f"\n=== HA48 ({len(ha48_triples)} triples) ===")
    rho_48, p_48, ci_48, n_48 = run_g_eval_on_dataset(
        "HA48", ha48_triples, OUT_CSV_HA48, client
    )
    print(f"  ρ={rho_48:+.4f} (p={p_48:.4g}) 95%CI=[{ci_48[0]:+.3f}, {ci_48[1]:+.3f}] n={n_48}")

    print(f"\n=== HA20 ({len(ha20_triples)} triples) ===")
    rho_20, p_20, ci_20, n_20 = run_g_eval_on_dataset(
        "HA20", ha20_triples, OUT_CSV_HA20, client
    )
    print(f"  ρ={rho_20:+.4f} (p={p_20:.4g}) 95%CI=[{ci_20[0]:+.3f}, {ci_20[1]:+.3f}] n={n_20}")

    # Summary MD
    elapsed = time.time() - t0
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("# G-Eval (LLM-as-judge) Baseline Results\n\n")
        f.write(f"- model: {GEVAL_MODEL}\n")
        f.write(f"- n_samples per triple: {GEVAL_N_SAMPLES}\n")
        f.write(f"- elapsed: {elapsed:.1f} sec\n\n")
        f.write("| dataset | n | Spearman ρ | 95% CI | p |\n")
        f.write("|---|---|---|---|---|\n")
        f.write(
            f"| HA48 | {n_48} | {rho_48:+.4f} | "
            f"[{ci_48[0]:+.3f}, {ci_48[1]:+.3f}] | {p_48:.4g} |\n"
        )
        f.write(
            f"| HA20 | {n_20} | {rho_20:+.4f} | "
            f"[{ci_20[0]:+.3f}, {ci_20[1]:+.3f}] | {p_20:.4g} |\n"
        )

    print(f"\n[done] Summary: {OUT_MD.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
