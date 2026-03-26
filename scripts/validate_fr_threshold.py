"""validate_fr_threshold.py — full_recall 閾値の緩和検証

primary path の full_recall 閾値のみを変動させ、回収数と偽ヒットを定量評価する。
演算子フレーム回収パスは固定 (fr=0.25, dr=0.10, overlap=2) のまま。

比較ポイント:
  fr=0.35 (baseline): 現行閾値
  fr=0.30:            保守的緩和
  fr=0.25:            積極的緩和
  fr=0.20:            限界探索

Usage:
    python scripts/validate_fr_threshold.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    OPERATOR_CATALOG,
    _NEGATION_POLARITY_FORMS,
    _SPECULATIVE_EXCLUSIONS,
    _MIN_OVERLAP,
    _extract_content_bigrams,
    _expand_with_synonyms,
    _split_sentences,
    detect,
    detect_operator,
)
from ugh_calculator import calculate
from decider import decide

# --- 定数 ---
BASE_DIR = Path(__file__).resolve().parent.parent / "data"
QUESTIONS_PATH = BASE_DIR / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
RESPONSES_PATH = BASE_DIR / "phase_c_scored_v1_t0_only.jsonl"
HA20_PATH = BASE_DIR / "human_annotation_20" / "human_annotation_20_completed.csv"

# fr閾値ポイント (primary path の full_recall のみ変動)
FR_THRESHOLDS = [0.35, 0.30, 0.25, 0.20]

# 固定パラメータ (primary path)
PRIMARY_DIRECT_RECALL = 0.15
PRIMARY_MIN_OVERLAP = _MIN_OVERLAP  # 3

# 固定パラメータ (operator recovery path) — 変更しない
OP_DIRECT_RECALL = 0.10
OP_FULL_RECALL = 0.25
OP_MIN_OVERLAP = 2


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
    data = {}
    with open(HA20_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            data[row["id"]] = {
                "human_score": float(row["human_score"]),
            }
    return data


# --- 否定チェック (detector.py の _response_has_negation 再現) ---
def _response_has_negation(
    response_text: str,
    concept_bigrams: Optional[set] = None,
) -> bool:
    if concept_bigrams:
        for sent in _split_sentences(response_text):
            clauses = re.split(r'(?:が、|しかし、|ただし、|けれど、|一方、)', sent)
            for clause in clauses:
                clause = clause.strip()
                if not clause:
                    continue
                if not any(bg in clause for bg in concept_bigrams):
                    continue
                cleaned = clause
                for excl in _SPECULATIVE_EXCLUSIONS:
                    cleaned = cleaned.replace(excl, "")
                if any(form in cleaned for form in _NEGATION_POLARITY_FORMS):
                    return True
        return False
    cleaned = response_text
    for excl in _SPECULATIVE_EXCLUSIONS:
        cleaned = cleaned.replace(excl, "")
    return any(form in cleaned for form in _NEGATION_POLARITY_FORMS)


# --- 閾値可変の命題検出 (演算子フレーム回収込み) ---
def check_propositions_with_fr(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
    full_recall_thresh: float = 0.35,
) -> Tuple[int, List[int], List[int], List[dict]]:
    """check_propositions の fr閾値可変版。演算子フレーム回収パスを含む。"""

    _NEGATION_CUES = ["ではなく", "ではない", "のではなく", "誤り", "不適切",
                      "批判", "安易", "短絡"]

    details: List[dict] = []

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
                return 0, [], miss_ids, [
                    {"prop_idx": i, "disqualified": True}
                    for i in range(len(core_props))
                ]

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

        min_required = min(PRIMARY_MIN_OVERLAP, len(prop_bigrams))
        hit_via = ""

        # --- Primary path (fr閾値のみ可変) ---
        if (d_recall >= PRIMARY_DIRECT_RECALL
                and f_recall >= full_recall_thresh
                and overlap_count >= min_required):
            hit_ids.append(i)
            hit_via = "primary"
        else:
            # --- 演算子フレーム回収パス (固定閾値) ---
            op = detect_operator(prop)
            op_recovered = False
            if op is not None:
                markers = OPERATOR_CATALOG[op.family]["response_markers"]
                marker_found = False
                for sent in _split_sentences(response_text):
                    if any(bg in sent for bg in overlap_set):
                        if any(m in sent for m in markers):
                            marker_found = True
                            break
                if (marker_found
                        and d_recall >= OP_DIRECT_RECALL
                        and f_recall >= OP_FULL_RECALL
                        and overlap_count >= OP_MIN_OVERLAP):
                    # 極性検証
                    _NEG_DEONTIC = (
                        "べきではない", "すべきではない",
                        "べきでない", "すべきでない",
                        "べきじゃない", "すべきじゃない",
                    )
                    has_neg_deontic = any(nd in prop for nd in _NEG_DEONTIC)
                    needs_polarity = (
                        OPERATOR_CATALOG[op.family]["effect"] == "polarity_flip"
                        or has_neg_deontic
                    )
                    polarity_ok = True
                    if needs_polarity and not _response_has_negation(
                        response_text, overlap_set
                    ):
                        polarity_ok = False

                    # 逆極性検証
                    _POS_DEONTIC = ("すべき", "べき")
                    is_positive_deontic = (
                        any(pd in prop for pd in _POS_DEONTIC)
                        and not has_neg_deontic
                    )
                    reverse_polarity_fail = False
                    if is_positive_deontic and _response_has_negation(
                        response_text, overlap_set
                    ):
                        reverse_polarity_fail = True

                    if polarity_ok and not reverse_polarity_fail:
                        hit_ids.append(i)
                        hit_via = "operator_recovery"
                        op_recovered = True

            if not op_recovered:
                miss_ids.append(i)

        details.append({
            "prop_idx": i,
            "prop_text": prop,
            "n_bigrams": len(prop_bigrams),
            "direct_recall": round(d_recall, 4),
            "full_recall": round(f_recall, 4),
            "overlap": overlap_count,
            "min_required": min_required,
            "hit": hit_via != "",
            "hit_via": hit_via,
            "matched_bigrams": sorted(overlap_set),
        })

    return len(hit_ids), hit_ids, miss_ids, details


def run_full_audit(qid: str, response_text: str, question_meta: dict,
                   full_recall_thresh: float = 0.35) -> dict:
    """1件の監査を fr閾値可変で実行"""
    evidence = detect(qid, response_text, question_meta)
    core_props = question_meta.get("core_propositions", [])
    disqualifying = question_meta.get("disqualifying_shortcuts", [])
    acceptable_variants = question_meta.get("acceptable_variants", [])

    hits, hit_ids, miss_ids, prop_details = check_propositions_with_fr(
        response_text, core_props, disqualifying, acceptable_variants,
        full_recall_thresh=full_recall_thresh,
    )

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
    if human_score <= 1.5:
        return decision == "regenerate"
    if human_score <= 2.5:
        return decision in ("rewrite", "regenerate")
    if human_score <= 3.5:
        return decision in ("accept", "rewrite")
    return decision == "accept"


def compute_rho(audit_results: Dict[str, dict], ha20: Dict[str, dict]) -> Tuple[float, int, int]:
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
    print("=== fr閾値 (full_recall) 緩和検証 ===\n")

    questions = load_questions()
    responses = load_responses()
    ha20 = load_ha20()

    print(f"Questions: {len(questions)}, Responses: {len(responses)}, HA20: {len(ha20)}")

    # 各fr閾値で実行
    all_results: Dict[str, Dict[str, dict]] = {}
    all_hit_sets: Dict[str, set] = {}
    all_prop_details: Dict[str, Dict[str, List[dict]]] = {}
    all_decisions: Dict[str, Dict[str, str]] = {}  # fr -> {qid -> decision}

    for fr in FR_THRESHOLDS:
        label = f"{fr:.2f}"
        print(f"\n--- fr={label} (primary: dr≥{PRIMARY_DIRECT_RECALL}, "
              f"fr≥{fr}, ovl≥{PRIMARY_MIN_OVERLAP}) ---")

        results = {}
        hit_set = set()
        prop_details_all = {}
        decisions = {}
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
                    full_recall_thresh=fr,
                )
                results[qid] = result
                prop_details_all[qid] = result["prop_details"]
                decisions[qid] = result["policy"]["decision"]

                for d in result["prop_details"]:
                    if d.get("hit"):
                        hit_set.add((qid, d["prop_idx"]))
            except Exception as e:
                errors.append(f"{qid}: {e}")

        if errors:
            print(f"  ERRORS: {len(errors)}")
            for err in errors[:5]:
                print(f"    {err}")

        all_results[label] = results
        all_hit_sets[label] = hit_set
        all_prop_details[label] = prop_details_all
        all_decisions[label] = decisions

        # 統計
        total_hits = sum(r["evidence"].propositions_hit for r in results.values())
        total_props = sum(r["evidence"].propositions_total for r in results.values())
        hit_rate = total_hits / total_props if total_props else 0

        rho, rho_match, rho_total = compute_rho(results, ha20)

        # f4 一致率
        f4_count = sum(
            1 for r in results.values()
            if r["evidence"].f4_premise > 0
        )

        print(f"  ヒット: {total_hits}/{total_props} = {hit_rate:.1%}")
        print(f"  ρ(HA20): {rho_match}/{rho_total} = {rho:.2f}")
        print(f"  f4発火: {f4_count}/102")

    # === 差分分析 ===
    baseline_label = "0.35"
    baseline_set = all_hit_sets[baseline_label]
    baseline_decisions = all_decisions[baseline_label]

    print(f"\n{'='*70}")
    print("差分分析 (baseline = fr=0.35)")
    print(f"{'='*70}")

    # 回帰チェック
    for fr in FR_THRESHOLDS[1:]:
        label = f"{fr:.2f}"
        regression = baseline_set - all_hit_sets[label]
        if regression:
            print(f"\n  ⚠ fr={label} 回帰あり: {len(regression)}件")
            for qid, pidx in sorted(regression):
                print(f"    {qid}:p{pidx}")
        else:
            print(f"\n  ✓ fr={label} 回帰なし (ベースライン全件維持)")

    # 新規ヒット一覧
    new_hits_by_label: Dict[str, list] = {}
    for fr in FR_THRESHOLDS[1:]:
        label = f"{fr:.2f}"
        new_hits = all_hit_sets[label] - baseline_set
        new_list = []
        for qid, pidx in sorted(new_hits):
            details = all_prop_details[label][qid]
            d = next((x for x in details if x["prop_idx"] == pidx), {})

            # response抜粋 (matched bigrams を含む文を抽出)
            resp_text = responses[qid].get("response", "")
            response_excerpt = ""
            matched = d.get("matched_bigrams", [])
            if matched:
                for sent in _split_sentences(resp_text):
                    if any(bg in sent for bg in matched):
                        response_excerpt = sent[:120]
                        break

            new_list.append({
                "qid": qid,
                "prop_idx": pidx,
                "prop_text": d.get("prop_text", ""),
                "direct_recall": d.get("direct_recall", 0),
                "full_recall": d.get("full_recall", 0),
                "overlap": d.get("overlap", 0),
                "hit_via": d.get("hit_via", ""),
                "matched_bigrams": d.get("matched_bigrams", []),
                "response_excerpt": response_excerpt,
            })
        new_hits_by_label[label] = new_list
        print(f"\n  fr={label} 新規ヒット: {len(new_list)}件")
        for nh in new_list:
            print(f"    {nh['qid']}:p{nh['prop_idx']} "
                  f"d={nh['direct_recall']:.3f} f={nh['full_recall']:.3f} "
                  f"ovl={nh['overlap']} via={nh['hit_via']}")
            print(f"      命題: 「{nh['prop_text'][:60]}」")
            if nh["matched_bigrams"]:
                print(f"      matched: {', '.join(nh['matched_bigrams'][:15])}")
            if nh["response_excerpt"]:
                print(f"      回答抜粋: {nh['response_excerpt'][:80]}...")

    # Decision 変化
    print(f"\n{'='*70}")
    print("Decision 変化")
    print(f"{'='*70}")
    for fr in FR_THRESHOLDS[1:]:
        label = f"{fr:.2f}"
        decisions = all_decisions[label]
        changed = []
        for qid in sorted(baseline_decisions.keys()):
            if qid in decisions and baseline_decisions[qid] != decisions[qid]:
                changed.append((qid, baseline_decisions[qid], decisions[qid]))
        print(f"\n  fr={label}: {len(changed)}件のdecision変化")
        for qid, old, new in changed:
            print(f"    {qid}: {old} → {new}")

    # === CSV/MD 出力 ===
    output_dir = Path(__file__).resolve().parent.parent / "analysis" / "fr_threshold"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Part A: 全命題の判定結果
    part_a_rows = []
    for fr in FR_THRESHOLDS:
        label = f"{fr:.2f}"
        results = all_results[label]
        total_hits = sum(r["evidence"].propositions_hit for r in results.values())
        total_props = sum(r["evidence"].propositions_total for r in results.values())
        rho, rho_match, rho_total = compute_rho(results, ha20)
        hits_new = len(all_hit_sets[label] - baseline_set) if label != baseline_label else 0

        # f4一致率
        f4_count = sum(
            1 for r in results.values()
            if r["evidence"].f4_premise > 0
        )

        # decision変化数
        decisions = all_decisions[label]
        decision_changes = sum(
            1 for qid in baseline_decisions
            if qid in decisions and baseline_decisions[qid] != decisions[qid]
        ) if label != baseline_label else 0

        part_a_rows.append({
            "fr_threshold": label,
            "hits": total_hits,
            "total": total_props,
            "hit_rate": round(total_hits / total_props, 4) if total_props else 0,
            "new_hits": hits_new,
            "rho": round(rho, 2),
            "rho_detail": f"{rho_match}/{rho_total}",
            "f4_fired": f4_count,
            "decision_changes": decision_changes,
        })

    with open(output_dir / "part_a_comparison.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=part_a_rows[0].keys())
        writer.writeheader()
        writer.writerows(part_a_rows)
    print(f"\n✓ Part A: {output_dir / 'part_a_comparison.csv'}")

    # Part B: 新規ヒット一覧
    part_b_rows = []
    for fr in FR_THRESHOLDS[1:]:
        label = f"{fr:.2f}"
        for nh in new_hits_by_label[label]:
            part_b_rows.append({
                "fr_threshold": label,
                "qid": nh["qid"],
                "prop_idx": nh["prop_idx"],
                "proposition": nh["prop_text"],
                "direct_recall": nh["direct_recall"],
                "full_recall": nh["full_recall"],
                "overlap": nh["overlap"],
                "hit_via": nh["hit_via"],
                "matched_bigrams": ", ".join(nh["matched_bigrams"][:10]),
                "response_excerpt": nh["response_excerpt"][:200],
                "judgment": "",
                "cause": "",
            })

    if part_b_rows:
        with open(output_dir / "part_b_new_hits.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=part_b_rows[0].keys())
            writer.writeheader()
            writer.writerows(part_b_rows)
        print(f"✓ Part B: {output_dir / 'part_b_new_hits.csv'}")

    # HA20 詳細
    print(f"\n{'='*70}")
    print("HA20 詳細")
    print(f"{'='*70}")
    for fr in FR_THRESHOLDS:
        label = f"{fr:.2f}"
        results = all_results[label]
        print(f"\nfr={label}:")
        print(f"  {'qid':>5} {'human':>5} {'dE':>6} {'hits':>4} {'decision':>12} {'match':>5}")
        for qid in sorted(ha20.keys()):
            if qid in results:
                r = results[qid]
                hs = ha20[qid]["human_score"]
                dec = r["policy"]["decision"]
                dm = "✓" if direction_match(hs, dec) else "✗"
                print(f"  {qid:>5} {hs:>5.1f} {r['state'].delta_e:>6.3f} "
                      f"{r['evidence'].propositions_hit:>1}/{r['evidence'].propositions_total:>1} "
                      f"{dec:>12} {dm:>5}")

    # サマリー出力
    print(f"\n{'='*70}")
    print("サマリーテーブル")
    print(f"{'='*70}")
    print(f"{'指標':<20} ", end="")
    for fr in FR_THRESHOLDS:
        print(f"{'fr=' + f'{fr:.2f}':>12}", end="")
    print()
    print("-" * 70)
    # ヒット率
    print(f"{'命題ヒット率':<20} ", end="")
    for row in part_a_rows:
        print(f"  {row['hits']}/{row['total']}", end="")
    print()
    print(f"{'ヒット率 %':<20} ", end="")
    for row in part_a_rows:
        pct = f"{row['hit_rate']:.1%}"
        print(f"  {pct:>10}", end="")
    print()
    print(f"{'新規ヒット':<20} ", end="")
    for row in part_a_rows:
        s = f"+{row['new_hits']}" if row["new_hits"] > 0 else "—"
        print(f"  {s:>10}", end="")
    print()
    print(f"{'ρ (HA20)':<20} ", end="")
    for row in part_a_rows:
        print(f"  {row['rho_detail']:>10}", end="")
    print()
    print(f"{'f4発火':<20} ", end="")
    for row in part_a_rows:
        print(f"  {row['f4_fired']}/102", end="")
    print()
    print(f"{'decision変化':<20} ", end="")
    for row in part_a_rows:
        s = f"{row['decision_changes']}件" if row["decision_changes"] > 0 else "—"
        print(f"  {s:>10}", end="")
    print()

    print(f"\n完了。出力先: {output_dir}")


if __name__ == "__main__":
    main()
