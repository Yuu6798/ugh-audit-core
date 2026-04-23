"""analysis/cohen_kappa.py — annotator 間 agreement の Cohen's κ 計算

HA100 拡充で第二 annotator を導入した際の inter-annotator agreement
(IAA) を計測する。HA48 schema
(``id, category, S, C, O, propositions_hit, notes``) の ordinal / Likert
列に対し、nominal / linear weighted / quadratic weighted κ をすべて算出する。

**論文上の位置付け**: NLP2027 年次大会版の Limitations で
「単一 annotator のため IAA は現時点で未測定」と明記する一方、
journal 拡張版では本 script の出力 (κ ≥ 0.6 を目標) を報告する前提の
インフラ整備。

使い方:
  python analysis/cohen_kappa.py <annotator_a.csv> <annotator_b.csv> \\
      --col O --weights linear --bootstrap 1000

  # HA48 schema の標準 3 列すべてで計測:
  python analysis/cohen_kappa.py a.csv b.csv --cols O,S,C

出力:
  - stdout: κ, 95% bootstrap CI, percent agreement, confusion matrix
  - --out-csv 指定時: `{out}.csv` に per-item diff も書き出し
    (reconciliation 用)

CSV 要件:
  両 annotator CSV は ``id`` 列を共有し、指定列 (``--col`` / ``--cols``) が
  float として parse 可能である必要がある (HA48 schema と同じ 1-5 Likert)。
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

WeightScheme = Literal["nominal", "linear", "quadratic"]


@dataclass(frozen=True)
class KappaResult:
    column: str
    n: int
    kappa: float
    weight_scheme: WeightScheme
    percent_agreement: float
    ci_95: Tuple[float, float]  # bootstrap 95%
    confusion: Dict[Tuple[int, int], int]


# --- Core Cohen's κ computation ---


def _weight(scheme: WeightScheme, a: int, b: int, k: int) -> float:
    """disagreement weight w(a, b). 1.0 = 完全不一致、0.0 = 一致。

    - nominal:   w = 0 if a == b else 1
    - linear:    w = |a - b| / (k - 1)
    - quadratic: w = ((a - b) / (k - 1))^2
    """
    if scheme == "nominal":
        return 0.0 if a == b else 1.0
    d = abs(a - b)
    denom = max(1, k - 1)
    if scheme == "linear":
        return d / denom
    # quadratic
    return (d / denom) ** 2


def cohen_kappa(
    a: List[int], b: List[int], *,
    weight_scheme: WeightScheme = "linear",
    categories: Optional[List[int]] = None,
) -> float:
    """Cohen's weighted κ。a, b は同長の ordinal rating list (整数)。

    categories が省略されれば a, b に出現する値の和集合を昇順で使う。
    """
    if len(a) != len(b):
        raise ValueError("a and b must have same length")
    if not a:
        return math.nan

    cats = sorted(set(a) | set(b)) if categories is None else sorted(categories)
    k = len(cats)
    idx = {c: i for i, c in enumerate(cats)}
    n = len(a)

    # observed agreement (disagreement) matrix
    obs = [[0] * k for _ in range(k)]
    for ai, bi in zip(a, b):
        obs[idx[ai]][idx[bi]] += 1

    # marginals
    row_marg = [sum(row) for row in obs]
    col_marg = [sum(obs[i][j] for i in range(k)) for j in range(k)]

    # P_o, P_e with weights (disagreement-based formulation of weighted κ)
    p_o_disagree = 0.0
    p_e_disagree = 0.0
    for i, ci in enumerate(cats):
        for j, cj in enumerate(cats):
            w = _weight(weight_scheme, ci, cj, k)
            if w == 0.0:
                continue
            p_o_disagree += w * obs[i][j] / n
            p_e_disagree += w * (row_marg[i] / n) * (col_marg[j] / n)

    if p_e_disagree == 0:
        return 1.0 if p_o_disagree == 0 else math.nan

    return 1.0 - (p_o_disagree / p_e_disagree)


def _bootstrap_ci(
    a: List[int], b: List[int], weight_scheme: WeightScheme,
    n_boot: int = 1000, alpha: float = 0.05, seed: int = 42,
) -> Tuple[float, float]:
    """κ の bootstrap 95% 信頼区間"""
    rng = random.Random(seed)
    n = len(a)
    if n == 0:
        return (math.nan, math.nan)
    kappas: List[float] = []
    for _ in range(n_boot):
        idxs = [rng.randrange(n) for _ in range(n)]
        sa = [a[i] for i in idxs]
        sb = [b[i] for i in idxs]
        try:
            k = cohen_kappa(sa, sb, weight_scheme=weight_scheme)
            if not math.isnan(k):
                kappas.append(k)
        except Exception:
            continue
    if not kappas:
        return (math.nan, math.nan)
    kappas.sort()
    lo_idx = int(n_boot * alpha / 2)
    hi_idx = int(n_boot * (1 - alpha / 2)) - 1
    return (kappas[max(0, lo_idx)], kappas[min(len(kappas) - 1, hi_idx)])


# --- CSV loading & alignment ---


def _load_col(path: Path, col: str) -> Dict[str, int]:
    """CSV から {id: int(col)} を返す。欠損や非数値はスキップ。"""
    result: Dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if "id" not in (reader.fieldnames or []):
            raise ValueError(f"{path}: 'id' 列が見つかりません")
        if col not in (reader.fieldnames or []):
            raise ValueError(f"{path}: '{col}' 列が見つかりません")
        for row in reader:
            qid = row.get("id", "").strip()
            if not qid:
                continue
            try:
                result[qid] = int(round(float(row[col])))
            except (ValueError, TypeError):
                continue
    return result


def _aligned_pairs(
    a_map: Dict[str, int], b_map: Dict[str, int]
) -> Tuple[List[str], List[int], List[int]]:
    common = sorted(set(a_map.keys()) & set(b_map.keys()))
    return common, [a_map[q] for q in common], [b_map[q] for q in common]


# --- Reporting ---


def _interpret_kappa(kappa: float) -> str:
    """Landis-Koch 1977 の慣例的な解釈ラベル。学術論文での参考値。"""
    if math.isnan(kappa):
        return "undefined"
    if kappa < 0.0:
        return "poor (worse than chance)"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost perfect"


def _format_confusion(
    a: List[int], b: List[int], categories: List[int]
) -> str:
    """confusion matrix を人間可読な markdown table に整形"""
    k = len(categories)
    idx = {c: i for i, c in enumerate(categories)}
    mat = [[0] * k for _ in range(k)]
    for ai, bi in zip(a, b):
        if ai in idx and bi in idx:
            mat[idx[ai]][idx[bi]] += 1
    header = "| A\\B | " + " | ".join(str(c) for c in categories) + " |"
    sep = "|" + "---|" * (k + 1)
    rows = [
        "| "
        + str(categories[i])
        + " | "
        + " | ".join(str(mat[i][j]) for j in range(k))
        + " |"
        for i in range(k)
    ]
    return "\n".join([header, sep] + rows)


def run_kappa_analysis(
    path_a: Path,
    path_b: Path,
    col: str,
    weight_scheme: WeightScheme,
    n_boot: int,
    out_csv: Optional[Path],
) -> KappaResult:
    a_map = _load_col(path_a, col)
    b_map = _load_col(path_b, col)
    ids, a, b = _aligned_pairs(a_map, b_map)
    n = len(a)

    if n == 0:
        print(f"[WARN] 共通 id が 0 件。{col} をスキップ。", file=sys.stderr)
        return KappaResult(
            column=col, n=0, kappa=math.nan, weight_scheme=weight_scheme,
            percent_agreement=math.nan, ci_95=(math.nan, math.nan),
            confusion={},
        )

    k = cohen_kappa(a, b, weight_scheme=weight_scheme)
    ci = _bootstrap_ci(a, b, weight_scheme, n_boot=n_boot)
    agree = sum(1 for ai, bi in zip(a, b) if ai == bi) / n

    cats = sorted(set(a) | set(b))
    conf = Counter(zip(a, b))

    # per-item diff CSV
    if out_csv is not None:
        with open(out_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", f"{col}_A", f"{col}_B", "diff"])
            for qid, ai, bi in zip(ids, a, b):
                w.writerow([qid, ai, bi, ai - bi])

    # stdout report
    print(f"=== Cohen's κ: column '{col}' ({weight_scheme} weights) ===")
    print(f"  n (aligned ids):    {n}")
    print(f"  percent agreement:  {agree:.3f} ({agree*100:.1f}%)")
    print(f"  κ:                  {k:+.4f}")
    print(f"  95% bootstrap CI:   [{ci[0]:+.3f}, {ci[1]:+.3f}] (n_boot={n_boot})")
    print(f"  interpretation:     {_interpret_kappa(k)} (Landis-Koch 1977)")
    print()
    print("  confusion matrix (rows=A, cols=B):")
    print(_format_confusion(a, b, cats))
    print()

    return KappaResult(
        column=col, n=n, kappa=k, weight_scheme=weight_scheme,
        percent_agreement=agree, ci_95=ci, confusion=dict(conf),
    )


# --- CLI ---


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("annotator_a", type=Path, help="第一 annotator CSV")
    p.add_argument("annotator_b", type=Path, help="第二 annotator CSV")
    p.add_argument(
        "--col", type=str, default=None,
        help="単一列の κ を計算 (例: O / S / C)",
    )
    p.add_argument(
        "--cols", type=str, default=None,
        help="複数列を一括で計算 (comma separated 例: 'O,S,C')",
    )
    p.add_argument(
        "--weights", type=str, default="linear",
        choices=["nominal", "linear", "quadratic"],
        help="ordinal data は linear (default), 厳しめは quadratic",
    )
    p.add_argument(
        "--bootstrap", type=int, default=1000,
        help="bootstrap 反復回数 (default: 1000)",
    )
    p.add_argument(
        "--out-csv", type=Path, default=None,
        help="per-item diff を CSV に書き出す (reconciliation 用)",
    )
    args = p.parse_args()

    if args.col is None and args.cols is None:
        args.col = "O"  # default to human quality score column

    cols = [args.col] if args.col else [c.strip() for c in args.cols.split(",")]

    for col in cols:
        run_kappa_analysis(
            args.annotator_a, args.annotator_b,
            col=col, weight_scheme=args.weights,
            n_boot=args.bootstrap, out_csv=args.out_csv,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
