"""analysis/calibrate_phase_e_thresholds.py — Phase E 閾値校正 (HA48)

mode_conditioned_grv の (collapse_risk, anchor_alignment) を使った
verdict_advisory の τ 閾値を HA48 (n=48) で校正する。

設計: docs/phase_e_verdict_integration.md §4

出力:
    - analysis/phase_e_calibration_grid.csv  (探索した全ペア)
    - analysis/phase_e_calibration_result.md (採用候補と leak check)

使い方:
    pip install sentence-transformers scipy
    python analysis/calibrate_phase_e_thresholds.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scipy.stats import (  # type: ignore[import-untyped]  # noqa: E402
    ConstantInputWarning,
    pearsonr,
    spearmanr,
)

warnings.filterwarnings("ignore", category=ConstantInputWarning)

from grv_calculator import compute_grv  # noqa: E402
from mode_grv import compute_mode_conditioned_grv  # noqa: E402

# --- データパス ---
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
V5_PATH = ROOT / "data" / "eval" / "audit_102_main_baseline_v5.csv"
Q_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
CANONICAL_PATH = (
    ROOT / "data" / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
)
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"

OUT_CSV = ROOT / "analysis" / "phase_e_calibration_grid.csv"
OUT_MD = ROOT / "analysis" / "phase_e_calibration_result.md"

# verdict quality rank (for full-set Spearman)
VERDICT_QUALITY_RANK = {
    "accept": 2,
    "rewrite": 1,
    "regenerate": 0,
}

# ΔE thresholds (HA48 校正済み, ugh_calculator.py と同一契約)
DELTA_E_ACCEPT = 0.10
DELTA_E_REWRITE = 0.25


def _primary_verdict(delta_e: Optional[float], c: Optional[float]) -> str:
    """docs/formulas.md の定義通りに primary verdict を導出する。"""
    if c is None or delta_e is None:
        return "degraded"
    if delta_e <= DELTA_E_ACCEPT:
        return "accept"
    if delta_e <= DELTA_E_REWRITE:
        return "rewrite"
    return "regenerate"


def _compute_delta_e(s: float, c: float) -> float:
    """ΔE 正規化 (ugh_calculator.calculate と同じ定数)."""
    weight_s = 2.0
    weight_c = 1.0
    max_dist = weight_s + weight_c  # = 3.0
    raw = weight_s * (1.0 - s) + weight_c * (1.0 - c)
    return max(0.0, min(1.0, raw / max_dist))


# --- データローダ ---


def load_ha48() -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def load_v5(ids: set) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(V5_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["id"] not in ids:
                continue
            try:
                s = float(row["S"])
                c = float(row["C"])
            except (KeyError, ValueError):
                continue
            result[row["id"]] = {"S": s, "C": c}
    return result


def load_q_meta(ids: set) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(Q_META_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["id"] in ids:
                result[rec["id"]] = rec
    return result


def load_canonical(ids: set) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(CANONICAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["id"] in ids and "mode_affordance" in rec:
                result[rec["id"]] = rec["mode_affordance"]
    return result


def load_responses(ids: set) -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    with open(RESPONSES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("question_id") or rec.get("id")
            if qid in ids:
                result[qid] = rec
    return result


# --- Row 構造 ---


@dataclass(frozen=True)
class Row:
    qid: str
    o: float
    s: float
    c: float
    delta_e: float
    verdict: str
    anchor_alignment: Optional[float]
    collapse_risk: Optional[float]


def build_rows() -> List[Row]:
    ha48 = load_ha48()
    ids = set(ha48.keys())
    v5 = load_v5(ids)
    q_meta = load_q_meta(ids)
    canonical = load_canonical(ids)
    responses = load_responses(ids)

    common = ids & v5.keys() & q_meta.keys() & canonical.keys() & responses.keys()
    print(
        f"データ: HA48={len(ha48)}, v5={len(v5)}, q_meta={len(q_meta)}, "
        f"canonical={len(canonical)}, responses={len(responses)}, overlap={len(common)}"
    )

    rows: List[Row] = []
    for qid in sorted(common):
        meta = q_meta[qid]
        resp = responses[qid]
        ma = canonical[qid]
        v = v5[qid]

        try:
            o = float(ha48[qid]["O"])
        except (KeyError, ValueError):
            continue

        s = v["S"]
        c = v["C"]
        delta_e = _compute_delta_e(s, c)
        verdict = _primary_verdict(delta_e, c)

        anchor: Optional[float] = None
        collapse: Optional[float] = None
        try:
            grv_r = compute_grv(
                question=meta["question"],
                response_text=resp.get("response", ""),
                question_meta=meta,
                metadata_source="inline",
            )
        except Exception as e:
            print(f"  grv failed for {qid}: {e}")
            grv_r = None

        if grv_r is not None:
            mcg = compute_mode_conditioned_grv(
                grv_result=grv_r,
                response_text=resp.get("response", ""),
                mode_affordance_primary=ma["primary"],
                action_required=ma.get("action_required", False),
            )
            if mcg is not None:
                anchor = mcg.anchor_alignment
                collapse = mcg.collapse_risk

        rows.append(
            Row(
                qid=qid,
                o=o,
                s=s,
                c=c,
                delta_e=delta_e,
                verdict=verdict,
                anchor_alignment=anchor,
                collapse_risk=collapse,
            )
        )

    return rows


# --- advisory 導出 ---


def derive_advisory_local(
    verdict: str,
    anchor: Optional[float],
    collapse: Optional[float],
    tau_collapse_high: float,
    tau_anchor_low: float,
) -> Tuple[str, List[str]]:
    """docs/phase_e_verdict_integration.md §6 の擬似コードを展開"""
    flags: List[str] = []
    if verdict != "accept":
        return verdict, flags

    advisory = verdict
    if collapse is not None and collapse >= tau_collapse_high:
        advisory = "rewrite"
        flags.append("mcg_collapse_downgrade")
    if anchor is not None and anchor <= tau_anchor_low:
        if advisory == "accept":
            advisory = "rewrite"
        flags.append("mcg_anchor_missing")
    return advisory, flags


# --- グリッド評価 ---


@dataclass
class GridResult:
    tau_collapse_high: float
    tau_anchor_low: float
    rho_accept_subset: Optional[float]
    rho_primary_full: float
    rho_advisory_full: float
    fire_rate: float
    low_quality_recall: float
    single_rule_fire_ratio: float
    n_accept: int
    n_full: int
    n_fire: int
    loo_rho_mean: Optional[float]
    loo_shrinkage: Optional[float]


def _safe_spearman(
    a: List[float], b: List[float]
) -> Tuple[Optional[float], Optional[float]]:
    if len(a) < 3:
        return None, None
    if len(set(a)) == 1 or len(set(b)) == 1:
        return None, None
    rho, p = spearmanr(a, b)
    if rho is None or (isinstance(rho, float) and rho != rho):  # NaN
        return None, None
    return float(rho), float(p)


def evaluate(
    rows: List[Row], tau_collapse_high: float, tau_anchor_low: float
) -> GridResult:
    # full set: degraded 除外
    full = [r for r in rows if r.verdict != "degraded"]
    # accept サブセット
    accept_rows = [r for r in full if r.verdict == "accept"]

    # advisory 計算
    advisory_full: List[str] = []
    advisory_accept: List[str] = []
    fire_both_rules = 0
    fire_single_rule = 0
    fire_count = 0
    low_quality_fires = 0
    low_quality_count = 0

    for r in full:
        adv, flags = derive_advisory_local(
            r.verdict, r.anchor_alignment, r.collapse_risk,
            tau_collapse_high, tau_anchor_low,
        )
        advisory_full.append(adv)

    for r in accept_rows:
        adv, flags = derive_advisory_local(
            r.verdict, r.anchor_alignment, r.collapse_risk,
            tau_collapse_high, tau_anchor_low,
        )
        advisory_accept.append(adv)
        fired = len(flags) > 0
        if fired:
            fire_count += 1
            if len(flags) == 2:
                fire_both_rules += 1
            else:
                fire_single_rule += 1
        if r.o <= 0.4:
            low_quality_count += 1
            if fired:
                low_quality_fires += 1

    # A. accept サブセット Spearman
    if accept_rows:
        accept_rank = [1 if a == "accept" else 0 for a in advisory_accept]
        o_accept = [r.o for r in accept_rows]
        rho_acc, _ = _safe_spearman(accept_rank, o_accept)
    else:
        rho_acc = None

    # B. フルセット Spearman
    primary_rank_full = [VERDICT_QUALITY_RANK[r.verdict] for r in full]
    advisory_rank_full = [VERDICT_QUALITY_RANK[a] for a in advisory_full]
    o_full = [r.o for r in full]
    rho_primary, _ = _safe_spearman(primary_rank_full, o_full)
    rho_adv, _ = _safe_spearman(advisory_rank_full, o_full)

    n_accept = len(accept_rows)
    fire_rate = fire_count / n_accept if n_accept > 0 else 0.0
    low_quality_recall = (
        low_quality_fires / low_quality_count if low_quality_count > 0 else 0.0
    )
    single_rule_fire_ratio = fire_single_rule / fire_count if fire_count > 0 else 0.0

    # LOO-CV on accept subset (re-evaluate rho leaving one accept row out)
    loo_rho_mean: Optional[float] = None
    loo_shrinkage: Optional[float] = None
    if rho_acc is not None and n_accept >= 5:
        rhos: List[float] = []
        for i in range(n_accept):
            sub_accept = accept_rows[:i] + accept_rows[i + 1:]
            adv_sub = []
            for r in sub_accept:
                adv, _ = derive_advisory_local(
                    r.verdict, r.anchor_alignment, r.collapse_risk,
                    tau_collapse_high, tau_anchor_low,
                )
                adv_sub.append(adv)
            rank_sub = [1 if a == "accept" else 0 for a in adv_sub]
            o_sub = [r.o for r in sub_accept]
            rho_sub, _ = _safe_spearman(rank_sub, o_sub)
            if rho_sub is not None:
                rhos.append(rho_sub)
        if rhos:
            loo_rho_mean = statistics.mean(rhos)
            loo_shrinkage = rho_acc - loo_rho_mean

    return GridResult(
        tau_collapse_high=tau_collapse_high,
        tau_anchor_low=tau_anchor_low,
        rho_accept_subset=rho_acc,
        rho_primary_full=rho_primary if rho_primary is not None else float("nan"),
        rho_advisory_full=rho_adv if rho_adv is not None else float("nan"),
        fire_rate=fire_rate,
        low_quality_recall=low_quality_recall,
        single_rule_fire_ratio=single_rule_fire_ratio,
        n_accept=n_accept,
        n_full=len(full),
        n_fire=fire_count,
        loo_rho_mean=loo_rho_mean,
        loo_shrinkage=loo_shrinkage,
    )


def grid_search(rows: List[Row]) -> List[GridResult]:
    # grid: 0.50..0.90 step 0.05 ; 0.10..0.50 step 0.05
    collapse_grid = [round(0.50 + 0.05 * i, 2) for i in range(9)]  # 9 pts
    anchor_grid = [round(0.10 + 0.05 * i, 2) for i in range(9)]  # 9 pts
    results: List[GridResult] = []
    for tc in collapse_grid:
        for ta in anchor_grid:
            results.append(evaluate(rows, tc, ta))
    return results


# --- Leak check ---


def leak_check(rows: List[Row]) -> Dict[str, Optional[float]]:
    full = [r for r in rows if r.verdict != "degraded"]
    pairs = [
        (r.c, r.anchor_alignment)
        for r in full
        if r.anchor_alignment is not None
    ]
    if len(pairs) < 5:
        return {"pearson": None, "spearman": None, "n": len(pairs)}
    cs = [p[0] for p in pairs]
    aa = [p[1] for p in pairs]
    try:
        pr, _ = pearsonr(cs, aa)
    except Exception:
        pr = None
    sp, _ = _safe_spearman(cs, aa)
    return {
        "pearson": float(pr) if pr is not None else None,
        "spearman": sp,
        "n": len(pairs),
    }


# --- 選択ロジック ---


def select_best(
    results: List[GridResult], leak_pearson: Optional[float]
) -> Tuple[Optional[GridResult], List[GridResult], str]:
    """docs/phase_e_verdict_integration.md §4「選択基準」に従う"""
    # leak check: |pearson_r| < 0.50
    if leak_pearson is not None and abs(leak_pearson) >= 0.50:
        return None, [], f"leak_fail: |pearson(C, anchor)|={abs(leak_pearson):.3f} >= 0.50"

    # プライマリの ρ (定数なので全候補で同じ)
    rho_primary = results[0].rho_primary_full if results else float("nan")

    # 条件: rho_advisory_full >= rho_primary_full - 0.02
    # 条件: 0.10 <= fire_rate <= 0.30
    candidates: List[GridResult] = []
    for r in results:
        if r.rho_advisory_full != r.rho_advisory_full:  # NaN
            continue
        if r.rho_advisory_full < rho_primary - 0.02:
            continue
        if not (0.10 <= r.fire_rate <= 0.30):
            continue
        candidates.append(r)

    if not candidates:
        return None, [], "no_candidates: fire_rate or rho_advisory_full constraint unmet"

    # Priority (desc): low_quality_recall > rho_accept_subset > -fire_rate > single_rule_fire_ratio
    def key(r: GridResult):
        return (
            r.low_quality_recall,
            r.rho_accept_subset if r.rho_accept_subset is not None else -1.0,
            -r.fire_rate,
            r.single_rule_fire_ratio,
        )

    candidates.sort(key=key, reverse=True)
    best = candidates[0]

    # LOO shrinkage が大きい場合は保守側に寄せる (>0.15 を目安)
    if best.loo_shrinkage is not None and best.loo_shrinkage > 0.15:
        conservative = [
            c for c in candidates
            if c.fire_rate <= best.fire_rate and c.tau_anchor_low <= best.tau_anchor_low
        ]
        if conservative:
            conservative.sort(key=lambda r: (r.fire_rate, r.tau_anchor_low))
            best = conservative[0]

    return best, candidates[: min(5, len(candidates))], "ok"


# --- 出力 ---


def write_grid_csv(results: List[GridResult]) -> None:
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "tau_collapse_high", "tau_anchor_low",
            "rho_accept_subset", "rho_primary_full", "rho_advisory_full",
            "fire_rate", "low_quality_recall", "single_rule_fire_ratio",
            "n_accept", "n_full", "n_fire",
            "loo_rho_mean", "loo_shrinkage",
        ])
        for r in results:
            w.writerow([
                f"{r.tau_collapse_high:.2f}",
                f"{r.tau_anchor_low:.2f}",
                f"{r.rho_accept_subset:.4f}" if r.rho_accept_subset is not None else "",
                f"{r.rho_primary_full:.4f}",
                f"{r.rho_advisory_full:.4f}",
                f"{r.fire_rate:.4f}",
                f"{r.low_quality_recall:.4f}",
                f"{r.single_rule_fire_ratio:.4f}",
                r.n_accept, r.n_full, r.n_fire,
                f"{r.loo_rho_mean:.4f}" if r.loo_rho_mean is not None else "",
                f"{r.loo_shrinkage:.4f}" if r.loo_shrinkage is not None else "",
            ])


def write_result_md(
    rows: List[Row],
    results: List[GridResult],
    best: Optional[GridResult],
    top_candidates: List[GridResult],
    leak: Dict[str, Optional[float]],
    status: str,
) -> None:
    from datetime import date
    today = date.today().isoformat()

    lines: List[str] = []
    lines.append("# Phase E Calibration Result")
    lines.append("")
    lines.append(f"生成日: {today}")
    lines.append(f"データソース: HA48 (n={len(rows)} rows loaded)")
    lines.append("")
    lines.append("## 採用閾値")
    lines.append("")
    if best is None:
        lines.append(f"**no-ship**: {status}")
        lines.append("")
        lines.append("閾値をハードコードせず、provisional 値で実装する（動作確認用）。")
        lines.append("")
    else:
        lines.append(f"- `τ_collapse_high = {best.tau_collapse_high:.2f}`")
        lines.append(f"- `τ_anchor_low   = {best.tau_anchor_low:.2f}`")
        lines.append("")

    lines.append("## メトリクス")
    lines.append("")
    lines.append("| 項目 | 値 |")
    lines.append("|---|---|")
    if best is not None:
        lines.append(f"| `rho_primary_full` | {best.rho_primary_full:.4f} |")
        lines.append(f"| `rho_advisory_full` | {best.rho_advisory_full:.4f} |")
        rho_acc_str = (
            f"{best.rho_accept_subset:.4f}"
            if best.rho_accept_subset is not None
            else "n/a"
        )
        lines.append(f"| `rho_accept_subset` | {rho_acc_str} |")
        lines.append(f"| `fire_rate` | {best.fire_rate:.3f} |")
        lines.append(f"| `low_quality_recall` | {best.low_quality_recall:.3f} |")
        lines.append(f"| `single_rule_fire_ratio` | {best.single_rule_fire_ratio:.3f} |")
        lines.append(f"| `n_accept` | {best.n_accept} |")
        lines.append(f"| `n_full` | {best.n_full} |")
        lines.append(f"| `n_fire` | {best.n_fire} |")
        if best.loo_rho_mean is not None:
            lines.append(f"| `loo_rho_mean` | {best.loo_rho_mean:.4f} |")
            lines.append(f"| `loo_shrinkage` | {best.loo_shrinkage:.4f} |")
    else:
        rho_primary = results[0].rho_primary_full if results else float("nan")
        lines.append(f"| `rho_primary_full` (参照) | {rho_primary:.4f} |")
    lines.append("")

    lines.append("## Leak check")
    lines.append("")
    pr = leak["pearson"]
    sp = leak["spearman"]
    lines.append(f"- `pearson_r(C, anchor_alignment) = {pr}`" if pr is not None else "- `pearson_r(C, anchor_alignment) = n/a`")
    lines.append(f"- `spearman_r(C, anchor_alignment) = {sp}`" if sp is not None else "- `spearman_r(C, anchor_alignment) = n/a`")
    lines.append(f"- `n = {leak['n']}`")
    lines.append("")
    if pr is not None and abs(pr) < 0.50:
        lines.append("解釈: `|r| < 0.50` のため leak は許容範囲。anchor_alignment は C とは独立の信号。")
    elif pr is not None:
        lines.append("解釈: `|r| >= 0.50` のため leak check fail。anchor は C と強く相関しており、独立信号とみなせない。")
    else:
        lines.append("解釈: leak check のサンプル不足。")
    lines.append("")

    lines.append("## 上位候補")
    lines.append("")
    lines.append("| τ_collapse | τ_anchor | ρ_adv_full | fire_rate | low_q_recall | loo_shr |")
    lines.append("|---|---|---|---|---|---|")
    for c in top_candidates:
        shr = f"{c.loo_shrinkage:.3f}" if c.loo_shrinkage is not None else "n/a"
        lines.append(
            f"| {c.tau_collapse_high:.2f} | {c.tau_anchor_low:.2f} | "
            f"{c.rho_advisory_full:.3f} | {c.fire_rate:.3f} | "
            f"{c.low_quality_recall:.3f} | {shr} |"
        )
    lines.append("")
    lines.append("## 採用理由")
    lines.append("")
    lines.append(f"- ステータス: `{status}`")
    if best is not None:
        lines.append(
            "- 候補中で `low_quality_recall` が最大、`fire_rate` は 10%〜30% 範囲内、"
            "`rho_advisory_full` が primary 基準 -0.02 の許容範囲内。"
        )
        lines.append(f"- HA48 (n={best.n_full}) で校正。Phase E.1。")
    else:
        lines.append(
            "- §4 の選択基準を満たす候補がないため、このバッチでは閾値を"
            "確定できない。provisional 値で実装し、HA96+ での再校正を待つ。"
        )
    lines.append("")
    lines.append("## 生データ")
    lines.append("")
    lines.append("- 探索した全ペア: [`phase_e_calibration_grid.csv`](phase_e_calibration_grid.csv)")
    lines.append("")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


# --- main ---


def main() -> None:
    rows = build_rows()
    if not rows:
        print("データなし。終了。")
        return

    # verdict 分布
    dist: Dict[str, int] = {}
    for r in rows:
        dist[r.verdict] = dist.get(r.verdict, 0) + 1
    print(f"verdict 分布: {dist}")
    print(f"anchor 欠損: {sum(1 for r in rows if r.anchor_alignment is None)}")
    print(f"collapse 欠損: {sum(1 for r in rows if r.collapse_risk is None)}")

    leak = leak_check(rows)
    print(f"leak check: pearson={leak['pearson']}, spearman={leak['spearman']}, n={leak['n']}")

    results = grid_search(rows)
    write_grid_csv(results)
    print(f"書き出し: {OUT_CSV}")

    best, top, status = select_best(results, leak["pearson"])
    write_result_md(rows, results, best, top, leak, status)
    print(f"書き出し: {OUT_MD}")

    if best is not None:
        print(
            f"\n採用: τ_collapse_high={best.tau_collapse_high:.2f}, "
            f"τ_anchor_low={best.tau_anchor_low:.2f}"
        )
        print(
            f"  rho_advisory_full={best.rho_advisory_full:.4f}, "
            f"rho_primary_full={best.rho_primary_full:.4f}, "
            f"fire_rate={best.fire_rate:.3f}"
        )
    else:
        print(f"\nno-ship: {status}")


if __name__ == "__main__":
    main()
