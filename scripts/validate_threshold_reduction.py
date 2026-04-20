"""validate_threshold_reduction.py — 命題照合閾値の引き下げ検証

3閾値構成 (θ=0.10 / 0.09 / 0.08) で batch_audit_102 を再実行し、
ヒット率・ρ・偽ヒットを比較する。

θ=0.10 (baseline): _MIN_OVERLAP=3, direct_recall≥0.15, full_recall≥0.35
θ=0.09:            _MIN_OVERLAP=3, direct_recall≥0.10, full_recall≥0.30
θ=0.08:            _MIN_OVERLAP=2, direct_recall≥0.10, full_recall≥0.25

Usage:
    python scripts/validate_threshold_reduction.py
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    _extract_content_bigrams,
    _expand_with_synonyms,
    _split_sentences,
    detect,
)
from ugh_calculator import calculate
from decider import decide

# --- 定数 ---
BASE_DIR = Path(__file__).resolve().parent.parent / "data"
QUESTIONS_PATH = BASE_DIR / "question_sets" / "ugh-audit-100q-v3-1.jsonl"
RESPONSES_PATH = BASE_DIR / "phase_c_scored_v1_t0_only.jsonl"
HA20_PATH = BASE_DIR / "human_annotation_20" / "human_annotation_20_completed.csv"

# Z_23 サブセット定義 (演算子なしの23問)
# round2_measurement_report.md 記載の問
Z_23_IDS = {
    "q003", "q004", "q005", "q006", "q007", "q011", "q012", "q014",
    "q015", "q016", "q017", "q019", "q026", "q028", "q036", "q039",
    "q040", "q041", "q044", "q045", "q066", "q067", "q074",
}

# 閾値構成
THRESHOLD_CONFIGS = {
    "0.10": {"min_overlap": 3, "direct_recall": 0.15, "full_recall": 0.35},
    "0.09": {"min_overlap": 3, "direct_recall": 0.10, "full_recall": 0.30},
    "0.08": {"min_overlap": 2, "direct_recall": 0.10, "full_recall": 0.25},
}


# --- データロード ---
def load_questions() -> Dict[str, dict]:
    data = {}
    with open(QUESTIONS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            data[obj["id"]] = obj
    return data


def load_responses() -> Dict[str, dict]:
    data = {}
    with open(RESPONSES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            data[obj["id"]] = obj
    return data


def load_ha20() -> Dict[str, dict]:
    """HA20アノテーションを読み込む"""
    data = {}
    with open(HA20_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["id"]] = {
                "human_score": float(row["human_score"]),
                "propositions_hit": row["propositions_hit"],
            }
    return data


# --- 閾値可変の命題検出 ---
def check_propositions_with_threshold(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
    min_overlap: int = 3,
    direct_recall_thresh: float = 0.15,
    full_recall_thresh: float = 0.35,
) -> Tuple[int, List[int], List[int], List[dict]]:
    """check_propositions の閾値可変版。各命題の詳細情報も返す。"""

    _NEGATION_CUES = ["ではなく", "ではない", "のではなく", "誤り", "不適切",
                      "批判", "安易", "短絡"]

    details = []

    if not core_props:
        return 0, [], [], details

    if disqualifying:
        for shortcut in disqualifying:
            if not shortcut or shortcut not in response_text:
                continue
            sentences = _split_sentences(response_text)
            is_negated = False
            for sent in sentences:
                if shortcut in sent:
                    context = sent.replace(shortcut, "")
                    if any(cue in context for cue in _NEGATION_CUES):
                        is_negated = True
                        break
            if not is_negated:
                miss_ids = list(range(len(core_props)))
                return 0, [], miss_ids, [{"prop_idx": i, "disqualified": True} for i in range(len(core_props))]

    resp_bigrams = _extract_content_bigrams(response_text)

    all_variant_bigrams: List[set] = []
    if acceptable_variants:
        for variant in acceptable_variants:
            if variant and variant in response_text:
                all_variant_bigrams.append(_extract_content_bigrams(variant))

    hit_ids: List[int] = []
    miss_ids: List[int] = []

    for i, prop in enumerate(core_props):
        prop_bigrams = _extract_content_bigrams(prop)
        if not prop_bigrams:
            miss_ids.append(i)
            details.append({"prop_idx": i, "empty": True})
            continue

        expanded = _expand_with_synonyms(prop_bigrams)
        for vbg in all_variant_bigrams:
            common = vbg & prop_bigrams
            if len(common) >= max(2, len(prop_bigrams) * 0.3):
                expanded |= vbg

        overlap_set = expanded & resp_bigrams
        overlap_count = len(overlap_set)
        direct_overlap = len(prop_bigrams & resp_bigrams)
        d_recall = direct_overlap / len(prop_bigrams)
        f_recall = overlap_count / len(prop_bigrams)
        min_required = min(min_overlap, len(prop_bigrams))

        hit = (d_recall >= direct_recall_thresh
               and f_recall >= full_recall_thresh
               and overlap_count >= min_required)

        if hit:
            hit_ids.append(i)
        else:
            miss_ids.append(i)

        details.append({
            "prop_idx": i,
            "prop_text": prop,
            "n_bigrams": len(prop_bigrams),
            "direct_recall": round(d_recall, 4),
            "full_recall": round(f_recall, 4),
            "overlap": overlap_count,
            "min_required": min_required,
            "hit": hit,
            "matched_bigrams": sorted(overlap_set),
        })

    return len(hit_ids), hit_ids, miss_ids, details


def run_full_audit(qid: str, response_text: str, question_meta: dict,
                   min_overlap: int = 3,
                   direct_recall_thresh: float = 0.15,
                   full_recall_thresh: float = 0.35) -> dict:
    """1件の監査を閾値可変で実行"""
    # detect() を使いつつ命題部分だけ上書き
    evidence = detect(qid, response_text, question_meta)
    core_props = question_meta.get("core_propositions", [])
    disqualifying = question_meta.get("disqualifying_shortcuts", [])
    acceptable_variants = question_meta.get("acceptable_variants", [])

    hits, hit_ids, miss_ids, prop_details = check_propositions_with_threshold(
        response_text, core_props, disqualifying, acceptable_variants,
        min_overlap=min_overlap,
        direct_recall_thresh=direct_recall_thresh,
        full_recall_thresh=full_recall_thresh,
    )

    # Evidence (frozen dataclass) の命題フィールドを上書き
    evidence = replace(
        evidence,
        propositions_hit=hits,
        propositions_total=len(core_props),
        hit_ids=hit_ids,
        miss_ids=miss_ids,
    )

    state = calculate(evidence)
    result = decide(state, evidence)

    return {
        "evidence": evidence,
        "state": state,
        "policy": result["policy"],
        "budget": result["budget"],
        "prop_details": prop_details,
    }


def direction_match(human_score: float, decision: str) -> bool:
    """human_score と decision の方向性が一致するか (test_pipeline.py と同一ロジック)"""
    if human_score <= 1.5:
        return decision == "regenerate"
    if human_score <= 2.5:
        return decision in ("rewrite", "regenerate")
    if human_score <= 3.5:
        return decision in ("accept", "rewrite")
    return decision == "accept"


def compute_rho(audit_results: Dict[str, dict], ha20: Dict[str, dict]) -> Tuple[float, int, int]:
    """HA20 の方向性一致率 (ρ) を算出。(rho, match_count, total) を返す。

    ρ = direction_match_count / total (0.90 = 18/20)
    """
    match_count = 0
    total = 0
    for qid, ha_data in ha20.items():
        if qid not in audit_results:
            continue
        total += 1
        decision = audit_results[qid]["policy"]["decision"]
        if direction_match(ha_data["human_score"], decision):
            match_count += 1

    rho = match_count / total if total > 0 else 0.0
    return rho, match_count, total


def main():
    print("=== 命題照合閾値 引き下げ検証 ===\n")

    questions = load_questions()
    responses = load_responses()
    ha20 = load_ha20()

    print(f"Questions: {len(questions)}, Responses: {len(responses)}, HA20: {len(ha20)}")

    # Z_23 サブセット検証
    z23_in_data = Z_23_IDS & set(responses.keys())
    print(f"Z_23 subset: {len(z23_in_data)} / {len(Z_23_IDS)} found in data")

    # 各閾値構成で実行
    all_results: Dict[str, Dict[str, dict]] = {}  # theta -> {qid -> result}
    all_hit_sets: Dict[str, set] = {}  # theta -> set of (qid, prop_idx)
    all_prop_details: Dict[str, Dict[str, List[dict]]] = {}  # theta -> {qid -> [detail]}

    for theta, config in THRESHOLD_CONFIGS.items():
        print(f"\n--- θ={theta} (min_overlap={config['min_overlap']}, "
              f"direct_recall≥{config['direct_recall']}, full_recall≥{config['full_recall']}) ---")

        results = {}
        hit_set = set()
        prop_details_all = {}
        errors = []

        for qid in sorted(responses.keys()):
            resp_data = responses[qid]
            response_text = resp_data.get("response", "")
            q_meta = questions.get(qid, resp_data)
            if q_meta is not resp_data:
                for key in ("trap_type", "question", "category"):
                    if not q_meta.get(key) and resp_data.get(key):
                        q_meta[key] = resp_data[key]
                if not q_meta.get("trap_type") and q_meta.get("original_trap_type"):
                    q_meta["trap_type"] = q_meta["original_trap_type"]

            try:
                result = run_full_audit(
                    qid, response_text, q_meta,
                    min_overlap=config["min_overlap"],
                    direct_recall_thresh=config["direct_recall"],
                    full_recall_thresh=config["full_recall"],
                )
                results[qid] = result
                prop_details_all[qid] = result["prop_details"]

                for d in result["prop_details"]:
                    if d.get("hit"):
                        hit_set.add((qid, d["prop_idx"]))
            except Exception as e:
                errors.append(f"{qid}: {e}")

        if errors:
            print(f"  ERRORS: {len(errors)}")
            for err in errors[:5]:
                print(f"    {err}")

        all_results[theta] = results
        all_hit_sets[theta] = hit_set
        all_prop_details[theta] = prop_details_all

        # 統計
        total_hits = sum(r["evidence"].propositions_hit for r in results.values())
        total_props = sum(r["evidence"].propositions_total for r in results.values())
        hit_rate = total_hits / total_props if total_props else 0

        z23_hits = sum(r["evidence"].propositions_hit for qid, r in results.items() if qid in Z_23_IDS)
        z23_total = sum(r["evidence"].propositions_total for qid, r in results.items() if qid in Z_23_IDS)
        z23_rate = z23_hits / z23_total if z23_total else 0

        rho, rho_match, rho_total = compute_rho(results, ha20)

        print(f"  全体: {total_hits}/{total_props} = {hit_rate:.3f}")
        print(f"  Z_23: {z23_hits}/{z23_total} = {z23_rate:.3f}")
        print(f"  ρ(direction match, HA20): {rho_match}/{rho_total} = {rho:.2f}")

    # === 差分分析 ===
    baseline_set = all_hit_sets["0.10"]

    print(f"\n{'='*60}")
    print("差分分析")
    print(f"{'='*60}")

    # 回帰チェック: ベースラインでhitだったものがmissになっていないか
    for theta in ["0.09", "0.08"]:
        regression = baseline_set - all_hit_sets[theta]
        if regression:
            print(f"\n  ⚠ θ={theta} 回帰あり: {len(regression)}件")
            for qid, pidx in sorted(regression):
                print(f"    {qid}:p{pidx}")
        else:
            print(f"\n  ✓ θ={theta} 回帰なし")

    # 新規ヒット一覧
    new_hits_by_theta: Dict[str, list] = {}
    for theta in ["0.09", "0.08"]:
        new_hits = all_hit_sets[theta] - baseline_set
        new_list = []
        for qid, pidx in sorted(new_hits):
            details = all_prop_details[theta][qid]
            d = next((x for x in details if x["prop_idx"] == pidx), {})
            new_list.append({
                "qid": qid,
                "prop_idx": pidx,
                "prop_text": d.get("prop_text", ""),
                "direct_recall": d.get("direct_recall", 0),
                "full_recall": d.get("full_recall", 0),
                "overlap": d.get("overlap", 0),
                "matched_bigrams": d.get("matched_bigrams", []),
            })
        new_hits_by_theta[theta] = new_list
        print(f"\n  θ={theta} 新規ヒット: {len(new_list)}件")
        for nh in new_list:
            print(f"    {nh['qid']}:p{nh['prop_idx']} "
                  f"d_rec={nh['direct_recall']:.3f} f_rec={nh['full_recall']:.3f} "
                  f"ovlp={nh['overlap']} "
                  f"「{nh['prop_text'][:50]}」")
            if nh["matched_bigrams"]:
                print(f"      matched: {', '.join(nh['matched_bigrams'][:15])}")

    # === CSV/MD 出力 ===
    output_dir = Path(__file__).resolve().parent.parent / "analysis" / "threshold_validation"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Part A: 3点比較テーブル
    part_a_rows = []
    for theta in ["0.10", "0.09", "0.08"]:
        results = all_results[theta]
        total_hits = sum(r["evidence"].propositions_hit for r in results.values())
        total_props = sum(r["evidence"].propositions_total for r in results.values())
        z23_hits = sum(r["evidence"].propositions_hit for qid, r in results.items() if qid in Z_23_IDS)
        z23_total = sum(r["evidence"].propositions_total for qid, r in results.items() if qid in Z_23_IDS)
        rho, rho_match, rho_total = compute_rho(results, ha20)
        hits_new = len(all_hit_sets[theta] - baseline_set) if theta != "0.10" else 0
        false_hits = 0  # 後で目視確認後に更新

        part_a_rows.append({
            "threshold": theta,
            "hit_rate_all": round(total_hits / total_props, 4) if total_props else 0,
            "hit_rate_z23": round(z23_hits / z23_total, 4) if z23_total else 0,
            "hits_total": total_hits,
            "hits_new": hits_new,
            "rho": round(rho, 2),
            "rho_p": f"{rho_match}/{rho_total}",
            "false_hits": false_hits,
        })

    with open(output_dir / "part_a_comparison.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=part_a_rows[0].keys())
        writer.writeheader()
        writer.writerows(part_a_rows)
    print(f"\nPart A written: {output_dir / 'part_a_comparison.csv'}")

    # Part B: 新規ヒット全件 (judgment は後で記入)
    part_b_rows = []
    for theta in ["0.09", "0.08"]:
        for nh in new_hits_by_theta[theta]:
            part_b_rows.append({
                "threshold": theta,
                "id": nh["qid"],
                "prop_idx": nh["prop_idx"],
                "proposition": nh["prop_text"],
                "matched_expression": ", ".join(nh["matched_bigrams"][:10]),
                "direct_recall": nh["direct_recall"],
                "full_recall": nh["full_recall"],
                "overlap": nh["overlap"],
                "judgment": "",  # 目視確認後に記入
            })

    if part_b_rows:
        with open(output_dir / "part_b_new_hits.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=part_b_rows[0].keys())
            writer.writeheader()
            writer.writerows(part_b_rows)
        print(f"Part B written: {output_dir / 'part_b_new_hits.csv'}")
    else:
        print("Part B: 新規ヒットなし")

    # HA20 詳細出力（ρ検証用）
    print(f"\n{'='*60}")
    print("HA20 詳細 (ΔE vs human_score)")
    print(f"{'='*60}")
    for theta in ["0.10", "0.09", "0.08"]:
        print(f"\nθ={theta}:")
        results = all_results[theta]
        print(f"  {'qid':>5} {'human':>5} {'dE':>6} {'hits':>4} {'decision':>12}")
        for qid in sorted(ha20.keys()):
            if qid in results:
                r = results[qid]
                hs = ha20[qid]["human_score"]
                print(f"  {qid:>5} {hs:>5.1f} {r['state'].delta_e:>6.3f} "
                      f"{r['evidence'].propositions_hit:>1}/{r['evidence'].propositions_total:>1} "
                      f"{r['policy']['decision']:>12}")


if __name__ == "__main__":
    main()
