"""HA48 偽ヒット / 偽ミス 不一致分析スクリプト

system hit/miss と human hit/miss の不一致パターンを HA48 全問で分析し、
4象限分類（TP/FP/FN/TN）の診断表とサマリーレポートを生成する。

出力:
  analysis/false_hit_analysis_ha48.csv — 全命題の4象限分類
  analysis/false_hit_summary.md — 集計レポート
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_DIR = ROOT / "analysis"

# --- Input paths ---
HA48_PATH = DATA / "human_annotation_48" / "annotation_48_merged.csv"
HA20_PATH = DATA / "human_annotation_20" / "human_annotation_20_completed.csv"
HA28_PATH = DATA / "human_annotation_28" / "annotation_28_results.csv"
BASELINE_CASCADE_PATH = DATA / "eval" / "audit_102_main_baseline_cascade.csv"
BASELINE_R4_PATH = DATA / "eval" / "audit_102_main_baseline_round4.csv"
QUESTIONS_PATH = DATA / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"

# --- Output paths ---
OUT_CSV = OUT_DIR / "false_hit_analysis_ha48.csv"
OUT_MD = OUT_DIR / "false_hit_summary.md"


def load_questions() -> Dict[str, List[str]]:
    """qid -> list of core_propositions"""
    result = {}
    with open(QUESTIONS_PATH) as f:
        for line in f:
            d = json.loads(line)
            result[d["id"]] = d["original_core_propositions"]
    return result


def load_ha48() -> Dict[str, dict]:
    """qid -> {id, category, S, C, O, propositions_hit, notes}"""
    result = {}
    with open(HA48_PATH) as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def load_ha20_ids() -> set:
    ids = set()
    with open(HA20_PATH) as f:
        for row in csv.DictReader(f):
            ids.add(row["id"])
    return ids


def load_ha28_ids() -> set:
    ids = set()
    with open(HA28_PATH) as f:
        for row in csv.DictReader(f):
            ids.add(row["qid"])
    return ids


def load_baseline(path: Path) -> Dict[str, dict]:
    result = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            result[row["id"]] = row
    return result


def parse_hit_ids(s: str) -> List[int]:
    """Parse '[0, 2]' or '[]' -> list of ints"""
    s = s.strip()
    if not s or s == "[]":
        return []
    return [int(x.strip()) for x in s.strip("[]").split(",") if x.strip()]


def parse_propositions_hit(s: str) -> Tuple[int, int]:
    """Parse '2/3' -> (hits, total)"""
    parts = s.strip().split("/")
    return int(parts[0]), int(parts[1])


def analyze():
    questions = load_questions()
    ha48 = load_ha48()
    ha20_ids = load_ha20_ids()
    baseline = load_baseline(BASELINE_CASCADE_PATH)
    baseline_r4 = load_baseline(BASELINE_R4_PATH)

    # Identify cascade_rescued propositions
    cascade_rescued = set()
    for qid, row in baseline.items():
        if "hit_sources" in row and row["hit_sources"]:
            hs = json.loads(row["hit_sources"])
            for pidx, src in hs.items():
                if src == "cascade_rescued":
                    cascade_rescued.add((qid, int(pidx)))

    # Identify relaxed tier1 candidates: props that are hit in cascade but miss in round4
    relaxed_candidates = set()
    for qid in ha48:
        c_hit_ids = set(parse_hit_ids(baseline[qid]["hit_ids"]))
        r4_hit_ids = set(parse_hit_ids(baseline_r4[qid]["hit_ids"]))
        for pidx in c_hit_ids - r4_hit_ids:
            if (qid, pidx) not in cascade_rescued:
                relaxed_candidates.add((qid, pidx))
        # Also track all extra hits (cascade vs round4) across all 102 questions
    all_extra_hits = set()
    for qid in baseline:
        c_hit_ids = set(parse_hit_ids(baseline[qid]["hit_ids"]))
        r4_hit_ids = set(parse_hit_ids(baseline_r4[qid]["hit_ids"]))
        for pidx in c_hit_ids - r4_hit_ids:
            all_extra_hits.add((qid, pidx))

    # Build per-proposition rows
    rows = []
    for qid in sorted(ha48.keys()):
        h_row = ha48[qid]
        b_row = baseline[qid]
        props = questions[qid]
        n_props = len(props)

        human_hits, human_total = parse_propositions_hit(h_row["propositions_hit"])
        sys_hit_ids = set(parse_hit_ids(b_row["hit_ids"]))
        sys_total = int(b_row["total"])

        # Verify total consistency
        assert sys_total == n_props == human_total, (
            f"{qid}: sys_total={sys_total}, n_props={n_props}, human_total={human_total}"
        )

        subgroup = "HA20" if qid in ha20_ids else "HA28"

        # hit_sources for each proposition
        hit_sources = {}
        if "hit_sources" in b_row and b_row["hit_sources"]:
            hit_sources = json.loads(b_row["hit_sources"])

        # Since we don't know WHICH propositions human judged as hit,
        # we compute bounds and per-proposition classification
        # For each proposition: system_hit is known (from hit_ids)
        # human_hit is unknown at proposition level
        #
        # We assign per-proposition quadrant as:
        #   system_hit=True: "sys_hit" (could be TP or FP)
        #   system_hit=False: "sys_miss" (could be TN or FN)
        # And at question level we compute aggregate bounds.

        for pidx in range(n_props):
            sys_hit = pidx in sys_hit_ids
            source = hit_sources.get(str(pidx), "")
            is_cascade = (qid, pidx) in cascade_rescued
            is_relaxed = (qid, pidx) in relaxed_candidates

            rows.append({
                "qid": qid,
                "subgroup": subgroup,
                "prop_index": pidx,
                "proposition": props[pidx],
                "n_props": n_props,
                "human_hits": human_hits,
                "human_hit_rate": round(human_hits / n_props, 4),
                "system_hit": 1 if sys_hit else 0,
                "hit_source": source,
                "cascade_rescued": 1 if is_cascade else 0,
                "relaxed_candidate": 1 if is_relaxed else 0,
            })

    # Write per-proposition CSV
    fieldnames = [
        "qid", "subgroup", "prop_index", "proposition", "n_props",
        "human_hits", "human_hit_rate", "system_hit", "hit_source",
        "cascade_rescued", "relaxed_candidate",
    ]
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # --- Question-level 4-quadrant analysis ---
    # For each question: compute TP/FP/FN/TN bounds
    q_analysis = []
    for qid in sorted(ha48.keys()):
        h_row = ha48[qid]
        b_row = baseline[qid]
        props = questions[qid]
        n = len(props)

        h_hits, _ = parse_propositions_hit(h_row["propositions_hit"])
        s_hits = int(b_row["hits"])
        subgroup = "HA20" if qid in ha20_ids else "HA28"

        # Bounds: TP ranges from max(0, s+h-n) to min(s, h)
        tp_min = max(0, s_hits + h_hits - n)
        tp_max = min(s_hits, h_hits)
        # Best case for system: max overlap
        fp_best = s_hits - tp_max  # minimum FP
        fn_best = h_hits - tp_max  # minimum FN
        tn_best = n - s_hits - fn_best
        # Worst case for system: min overlap
        fp_worst = s_hits - tp_min  # maximum FP
        fn_worst = h_hits - tp_min  # maximum FN
        tn_worst = n - s_hits - fn_worst

        q_analysis.append({
            "qid": qid,
            "subgroup": subgroup,
            "n_props": n,
            "human_hits": h_hits,
            "system_hits": s_hits,
            "human_hit_rate": round(h_hits / n, 4),
            "system_hit_rate": round(s_hits / n, 4),
            "rate_gap": round(s_hits / n - h_hits / n, 4),
            "tp_min": tp_min, "tp_max": tp_max,
            "fp_min": fp_best, "fp_max": fp_worst,
            "fn_min": fn_best, "fn_max": fn_worst,
            "tn_min": tn_worst, "tn_max": tn_best,
            "category": ha48[qid]["category"],
            "human_O": ha48[qid]["O"],
        })

    # --- Aggregate statistics ---
    def compute_agg(subset: List[dict]) -> dict:
        total_props = sum(q["n_props"] for q in subset)
        total_sys_hits = sum(q["system_hits"] for q in subset)
        total_human_hits = sum(q["human_hits"] for q in subset)

        # Best-case bounds (max TP)
        tp = sum(q["tp_max"] for q in subset)
        fp = sum(q["fp_min"] for q in subset)
        fn = sum(q["fn_min"] for q in subset)
        tn = total_props - tp - fp - fn

        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0

        # Worst-case bounds (min TP)
        tp_w = sum(q["tp_min"] for q in subset)
        fp_w = sum(q["fp_max"] for q in subset)
        fn_w = sum(q["fn_max"] for q in subset)
        tn_w = total_props - tp_w - fp_w - fn_w

        precision_w = tp_w / (tp_w + fp_w) if (tp_w + fp_w) > 0 else 1.0
        recall_w = tp_w / (tp_w + fn_w) if (tp_w + fn_w) > 0 else 1.0

        return {
            "n_questions": len(subset),
            "n_props": total_props,
            "sys_hits": total_sys_hits,
            "human_hits": total_human_hits,
            "sys_hit_rate": round(total_sys_hits / total_props, 4),
            "human_hit_rate": round(total_human_hits / total_props, 4),
            # Best case (max TP)
            "tp_best": tp, "fp_best": fp, "fn_best": fn, "tn_best": tn,
            "precision_best": round(precision, 4),
            "recall_best": round(recall, 4),
            # Worst case (min TP)
            "tp_worst": tp_w, "fp_worst": fp_w, "fn_worst": fn_w, "tn_worst": tn_w,
            "precision_worst": round(precision_w, 4),
            "recall_worst": round(recall_w, 4),
        }

    agg_all = compute_agg(q_analysis)
    agg_ha20 = compute_agg([q for q in q_analysis if q["subgroup"] == "HA20"])
    agg_ha28 = compute_agg([q for q in q_analysis if q["subgroup"] == "HA28"])

    # --- FP candidates: questions where system_hits > human_hits ---
    fp_questions = [q for q in q_analysis if q["system_hits"] > q["human_hits"]]
    # --- FN candidates: questions where human_hits > system_hits ---
    fn_questions = [q for q in q_analysis if q["human_hits"] > q["system_hits"]]

    # --- Relaxed +9 overlap check ---
    # Extra hits in cascade vs round4 across all 102 questions
    # Check which of these fall in HA48 and are potential FP
    relaxed_fp_warnings = []
    for qid in sorted(ha48.keys()):
        b_row = baseline[qid]
        sys_hit_ids = set(parse_hit_ids(b_row["hit_ids"]))
        r4_hit_ids = set(parse_hit_ids(baseline_r4[qid]["hit_ids"]))
        h_hits, _ = parse_propositions_hit(ha48[qid]["propositions_hit"])
        s_hits = int(b_row["hits"])
        extra = sys_hit_ids - r4_hit_ids
        if extra and s_hits > h_hits:
            for pidx in sorted(extra):
                source = hit_sources.get(str(pidx), "")
                relaxed_fp_warnings.append({
                    "qid": qid,
                    "prop_index": pidx,
                    "proposition": questions[qid][pidx] if pidx < len(questions[qid]) else "?",
                    "hit_source": json.loads(b_row["hit_sources"]).get(str(pidx), ""),
                    "is_cascade_rescued": (qid, pidx) in cascade_rescued,
                })

    # --- Generate summary report ---
    lines = []
    lines.append("# 偽ヒット / 偽ミス 不一致分析（HA48）")
    lines.append("")
    lines.append("## 概要")
    lines.append("")
    lines.append("system（cascade baseline）と human アノテーションの命題 hit/miss 不一致を分析。")
    lines.append("human は命題ヒット数のみ既知（どの命題が hit かは不明）のため、")
    lines.append("TP/FP/FN/TN は上界・下界の範囲として算出。")
    lines.append("")
    lines.append("- **Best case**: system と human の一致を最大化（TP_max = min(sys_hits, human_hits)）")
    lines.append("- **Worst case**: 一致を最小化（TP_min = max(0, sys_hits + human_hits - n)）")
    lines.append("")

    # Overall aggregate table
    lines.append("## 全体集計（HA48: 48問）")
    lines.append("")
    lines.append("| 指標 | Best case | Worst case |")
    lines.append("|------|-----------|------------|")
    lines.append(f"| 命題総数 | {agg_all['n_props']} | {agg_all['n_props']} |")
    lines.append(f"| system hit | {agg_all['sys_hits']} ({agg_all['sys_hit_rate']}) | {agg_all['sys_hits']} ({agg_all['sys_hit_rate']}) |")
    lines.append(f"| human hit | {agg_all['human_hits']} ({agg_all['human_hit_rate']}) | {agg_all['human_hits']} ({agg_all['human_hit_rate']}) |")
    lines.append(f"| TP | {agg_all['tp_best']} | {agg_all['tp_worst']} |")
    lines.append(f"| FP (偽ヒット) | {agg_all['fp_best']} | {agg_all['fp_worst']} |")
    lines.append(f"| FN (偽ミス) | {agg_all['fn_best']} | {agg_all['fn_worst']} |")
    lines.append(f"| TN | {agg_all['tn_best']} | {agg_all['tn_worst']} |")
    lines.append(f"| **Precision** | **{agg_all['precision_best']}** | **{agg_all['precision_worst']}** |")
    lines.append(f"| **Recall** | **{agg_all['recall_best']}** | **{agg_all['recall_worst']}** |")
    lines.append("")

    # Subgroup tables
    for name, agg in [("HA20（20問）", agg_ha20), ("HA28（28問）", agg_ha28)]:
        lines.append(f"## サブグループ: {name}")
        lines.append("")
        lines.append("| 指標 | Best case | Worst case |")
        lines.append("|------|-----------|------------|")
        lines.append(f"| 命題総数 | {agg['n_props']} | {agg['n_props']} |")
        lines.append(f"| system hit | {agg['sys_hits']} ({agg['sys_hit_rate']}) | {agg['sys_hits']} ({agg['sys_hit_rate']}) |")
        lines.append(f"| human hit | {agg['human_hits']} ({agg['human_hit_rate']}) | {agg['human_hits']} ({agg['human_hit_rate']}) |")
        lines.append(f"| TP | {agg['tp_best']} | {agg['tp_worst']} |")
        lines.append(f"| FP (偽ヒット) | {agg['fp_best']} | {agg['fp_worst']} |")
        lines.append(f"| FN (偽ミス) | {agg['fn_best']} | {agg['fn_worst']} |")
        lines.append(f"| TN | {agg['tn_best']} | {agg['tn_worst']} |")
        lines.append(f"| **Precision** | **{agg['precision_best']}** | **{agg['precision_worst']}** |")
        lines.append(f"| **Recall** | **{agg['recall_best']}** | **{agg['recall_worst']}** |")
        lines.append("")

    # FP candidate questions
    lines.append("## FP 候補一覧（system_hits > human_hits）")
    lines.append("")
    lines.append("system が human より多く hit している問。少なくとも (sys - human) 件の偽ヒットが存在。")
    lines.append("")
    lines.append("| qid | subgroup | category | n_props | sys_hits | human_hits | min_FP | system_hit_ids | hit_sources |")
    lines.append("|-----|----------|----------|---------|----------|------------|--------|----------------|-------------|")
    for q in sorted(fp_questions, key=lambda x: x["fp_min"], reverse=True):
        qid = q["qid"]
        b_row = baseline[qid]
        hs = json.loads(b_row["hit_sources"]) if b_row.get("hit_sources") else {}
        hit_ids_str = b_row["hit_ids"]
        sources_str = ", ".join(f"{k}:{v}" for k, v in sorted(hs.items()) if v != "miss")
        lines.append(
            f"| {qid} | {q['subgroup']} | {q['category']} | {q['n_props']} | "
            f"{q['system_hits']} | {q['human_hits']} | {q['fp_min']} | "
            f"{hit_ids_str} | {sources_str} |"
        )
    lines.append("")
    lines.append(f"**FP 候補問数: {len(fp_questions)}問、最小 FP 合計: {sum(q['fp_min'] for q in fp_questions)}件**")
    lines.append("")

    # FN candidate questions
    lines.append("## FN 候補一覧（human_hits > system_hits）")
    lines.append("")
    lines.append("human が system より多く hit している問。少なくとも (human - sys) 件の偽ミスが存在。")
    lines.append("")
    lines.append("| qid | subgroup | category | n_props | sys_hits | human_hits | min_FN | system_miss_ids | miss原因推定 |")
    lines.append("|-----|----------|----------|---------|----------|------------|--------|-----------------|-------------|")
    for q in sorted(fn_questions, key=lambda x: x["fn_min"], reverse=True):
        qid = q["qid"]
        b_row = baseline[qid]
        miss_ids = parse_hit_ids(b_row["miss_ids"])
        props = questions[qid]
        miss_props = [f"[{i}]{props[i][:20]}..." for i in miss_ids if i < len(props)]
        lines.append(
            f"| {qid} | {q['subgroup']} | {q['category']} | {q['n_props']} | "
            f"{q['system_hits']} | {q['human_hits']} | {q['fn_min']} | "
            f"{b_row['miss_ids']} | {'; '.join(miss_props)} |"
        )
    lines.append("")
    lines.append(f"**FN 候補問数: {len(fn_questions)}問、最小 FN 合計: {sum(q['fn_min'] for q in fn_questions)}件**")
    lines.append("")

    # Questions where system == human (potential offsetting FP/FN)
    match_questions = [q for q in q_analysis if q["system_hits"] == q["human_hits"]]
    mismatch_possible = [q for q in match_questions if q["tp_min"] < q["tp_max"]]
    lines.append("## 一致問（system_hits == human_hits）")
    lines.append("")
    lines.append(f"一致: {len(match_questions)}問（うち完全一致保証: "
                 f"{len([q for q in match_questions if q['tp_min'] == q['tp_max']])}問）")
    lines.append("")
    lines.append("ヒット数が一致しても、異なる命題を hit している可能性あり:")
    lines.append("")
    if mismatch_possible:
        lines.append("| qid | subgroup | n_props | hits | tp_min | tp_max | 相殺FP/FN可能数 |")
        lines.append("|-----|----------|---------|------|--------|--------|----------------|")
        for q in mismatch_possible:
            offset = q["tp_max"] - q["tp_min"]
            lines.append(
                f"| {q['qid']} | {q['subgroup']} | {q['n_props']} | {q['system_hits']} | "
                f"{q['tp_min']} | {q['tp_max']} | 0〜{offset} |"
            )
        lines.append("")

    # Relaxed / cascade overlap warnings
    lines.append("## 閾値緩和・cascade rescue との重複チェック")
    lines.append("")
    lines.append(f"cascade_rescued（HA48内）: {len([x for x in cascade_rescued if x[0] in ha48])}件")
    for qid, pidx in sorted(cascade_rescued):
        if qid in ha48:
            p = questions[qid][pidx] if pidx < len(questions[qid]) else "?"
            lines.append(f"  - {qid}[{pidx}]: {p}")
    lines.append("")

    if relaxed_fp_warnings:
        lines.append("### 警告: 閾値緩和/cascade で追加された hit が FP 候補に含まれる")
        lines.append("")
        lines.append("| qid | prop_index | proposition | hit_source | cascade_rescued |")
        lines.append("|-----|-----------|-------------|------------|-----------------|")
        for w in relaxed_fp_warnings:
            lines.append(
                f"| {w['qid']} | {w['prop_index']} | {w['proposition'][:30]}... | "
                f"{w['hit_source']} | {w['is_cascade_rescued']} |"
            )
        lines.append("")
    else:
        lines.append("閾値緩和/cascade で追加された hit が FP 候補に含まれるケースはなし。")
        lines.append("")

    # Per-question detail table
    lines.append("## 全48問 詳細一覧")
    lines.append("")
    lines.append("| qid | sub | cat | n | sys | human | gap | tp_max | fp_min | fn_min | tn_max | O |")
    lines.append("|-----|-----|-----|---|-----|-------|-----|--------|--------|--------|--------|---|")
    for q in q_analysis:
        lines.append(
            f"| {q['qid']} | {q['subgroup']} | {q['category'][:8]} | {q['n_props']} | "
            f"{q['system_hits']} | {q['human_hits']} | {q['rate_gap']:+.2f} | "
            f"{q['tp_max']} | {q['fp_min']} | {q['fn_min']} | {q['tn_max']} | {q['human_O']} |"
        )
    lines.append("")

    # Write MD
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"Generated: {OUT_CSV}")
    print(f"Generated: {OUT_MD}")
    print()
    print("=== Summary ===")
    print(f"HA48: {agg_all['n_questions']}問, {agg_all['n_props']}命題")
    print(f"  system hit rate: {agg_all['sys_hit_rate']}")
    print(f"  human hit rate:  {agg_all['human_hit_rate']}")
    print(f"  Precision (best): {agg_all['precision_best']}")
    print(f"  Recall (best):    {agg_all['recall_best']}")
    print(f"  FP candidates: {len(fp_questions)}問 (min {sum(q['fp_min'] for q in fp_questions)}件)")
    print(f"  FN candidates: {len(fn_questions)}問 (min {sum(q['fn_min'] for q in fn_questions)}件)")


if __name__ == "__main__":
    analyze()
