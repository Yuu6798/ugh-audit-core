from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from statistics import mean
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import audit as audit_module  # noqa: E402
import detector  # noqa: E402
from ugh_calculator import WEIGHT_C, WEIGHT_S  # noqa: E402

ORIGINAL_CHECK_PROPOSITIONS = detector.check_propositions


DATA_DIR = ROOT / "data"
QUESTION_META_PATH = DATA_DIR / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
PHASE_C_RAW_PATH = DATA_DIR / "phase_c_v0" / "phase_c_raw.jsonl"
HA20_PATH = DATA_DIR / "human_annotation_20" / "human_annotation_20_completed.csv"
GATE_SUMMARY_PATH = DATA_DIR / "gate_results" / "structural_gate_summary.csv"

OUT_DIR = ROOT / "analysis" / "threshold_validation" / "outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

RELAXED_BY_SIZE = (
    (8, 0.10, 0.30, 2),
    (5, 0.12, 0.30, 2),
)
LOW_SCORE_IDS = {"q015", "q024", "q032", "q095"}
MANUAL_EXCLUDE = {("q016", 2), ("q042", 1), ("q061", 2)}
GENERIC_CHUNKS = {
    "問題", "可能", "必要", "評価", "分析", "関係", "場合", "法的",
    "意図", "リスク", "監査", "性能", "AI", "意味",
}


def rankdata(values: List[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearmanr(values_a: List[float], values_b: List[float]) -> float:
    if len(values_a) != len(values_b) or len(values_a) < 2:
        return 0.0
    ra = rankdata(values_a)
    rb = rankdata(values_b)
    ma = mean(ra)
    mb = mean(rb)
    num = sum((a - ma) * (b - mb) for a, b in zip(ra, rb))
    den_a = math.sqrt(sum((a - ma) ** 2 for a in ra))
    den_b = math.sqrt(sum((b - mb) ** 2 for b in rb))
    if den_a == 0 or den_b == 0:
        return 0.0
    return num / (den_a * den_b)


def load_question_meta() -> Dict[str, dict]:
    meta: Dict[str, dict] = {}
    with QUESTION_META_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                meta[rec["id"]] = rec
    return meta


def load_responses(temperature: float = 0.0) -> Dict[str, str]:
    rows: Dict[str, str] = {}
    with PHASE_C_RAW_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("temperature") == temperature:
                rows[rec["id"]] = rec["response"]
    return rows


def load_ha20() -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    with HA20_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            hit_num, hit_den = row["propositions_hit"].split("/")
            row["human_score"] = float(row["human_score"])
            row["human_hit_rate"] = int(hit_num) / int(hit_den)
            rows[row["id"]] = row
    return rows


def load_gate_summary() -> Dict[str, dict]:
    rows: Dict[str, dict] = {}
    with GATE_SUMMARY_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("temperature") != "0.0":
                continue
            rows[row["id"]] = {
                "f1": float(row["f1_flag"]),
                "f2": float(row["f2_flag"]),
                "f3": float(row["f3_flag"]),
                "f4": float(row["f4_flag"]),
                "fail_max": float(row["fail_max"]),
            }
    return rows


def compute_s_from_gate(gate_row: dict) -> float:
    weighted = 5 * gate_row["f1"] + 25 * gate_row["f2"] + 5 * gate_row["f3"] + 5 * gate_row["f4"]
    return max(0.0, min(1.0, 1.0 - weighted / 40.0))


def direction_match(human_score: float, decision: str) -> bool:
    if human_score <= 1.5:
        return decision == "regenerate"
    if human_score <= 2.5:
        return decision in ("rewrite", "regenerate")
    if human_score <= 3.5:
        return decision in ("accept", "rewrite")
    return decision == "accept"


def compute_delta_e_a(s: float, c: float) -> float:
    return (WEIGHT_S * (1.0 - s) ** 2 + WEIGHT_C * (1.0 - c) ** 2) / (WEIGHT_S + WEIGHT_C)


def build_prop_bigram_df(meta_map: Dict[str, dict]) -> Counter:
    df: Counter = Counter()
    for meta in meta_map.values():
        for prop in meta.get("core_propositions", []):
            df.update(detector._extract_content_bigrams(prop))
            df.update(detector._extract_content_chunks(prop))
    return df


def best_sentence(response_text: str, overlap_set: set) -> str:
    best = ""
    best_score = -1
    for sent in detector._split_sentences(response_text):
        score = sum(1 for tok in overlap_set if tok in sent)
        if score > best_score:
            best_score = score
            best = sent
    return best.strip()


def proposition_metrics(
    response_text: str,
    prop: str,
    acceptable_variant_bigrams: List[set],
) -> dict:
    prop_bigrams = detector._extract_content_bigrams(prop)
    expanded = detector._expand_with_synonyms(prop_bigrams)
    for vbg in acceptable_variant_bigrams:
        common = vbg & prop_bigrams
        if len(common) >= max(2, len(prop_bigrams) * 0.3):
            expanded |= vbg

    resp_bigrams = detector._extract_content_bigrams(response_text)
    overlap_set = expanded & resp_bigrams
    direct_set = prop_bigrams & resp_bigrams
    prop_len = len(prop_bigrams)
    return {
        "prop_bigrams": sorted(prop_bigrams),
        "prop_bigram_count": prop_len,
        "expanded": sorted(expanded),
        "overlap_set": sorted(overlap_set),
        "overlap_count": len(overlap_set),
        "direct_overlap_set": sorted(direct_set),
        "direct_overlap_count": len(direct_set),
        "direct_recall": len(direct_set) / prop_len if prop_len else 0.0,
        "full_recall": len(overlap_set) / prop_len if prop_len else 0.0,
    }


def baseline_hit(metrics: dict) -> bool:
    return (
        metrics["direct_recall"] >= 0.15
        and metrics["full_recall"] >= 0.35
        and metrics["overlap_count"] >= min(detector._MIN_OVERLAP, metrics["prop_bigram_count"])
    )


def relaxed_thresholds(prop_bigram_count: int, op_family: Optional[str]) -> Tuple[float, float, int]:
    if op_family is not None:
        return 0.15, 0.35, detector._MIN_OVERLAP
    for size_floor, direct_t, full_t, overlap_t in RELAXED_BY_SIZE:
        if prop_bigram_count >= size_floor:
            return direct_t, full_t, overlap_t
    return 0.15, 0.35, detector._MIN_OVERLAP


_NEG_DEONTIC = (
    "べきではない", "すべきではない", "べきでない", "すべきでない",
    "べきじゃない", "すべきじゃない",
)
_POS_DEONTIC = ("すべき", "べき")


def _polarity_blocked(
    prop: str, op_family: Optional[str], response_text: str, overlap_set: set,
) -> bool:
    """極性検証: detector.check_propositions と同じガードを適用"""
    has_neg_deontic = any(nd in prop for nd in _NEG_DEONTIC)
    is_positive_deontic = (
        any(pd in prop for pd in _POS_DEONTIC) and not has_neg_deontic
    )
    needs_polarity = (
        (op_family is not None
         and detector.OPERATOR_CATALOG[op_family]["effect"] == "polarity_flip")
        or has_neg_deontic
    )
    if needs_polarity and not detector._response_has_negation(response_text, overlap_set):
        return True
    if is_positive_deontic and detector._response_has_negation(response_text, overlap_set):
        return True
    return False


def relaxed_hit(
    metrics: dict, op_family: Optional[str], response_text: str, prop: str = "",
) -> Tuple[bool, str]:
    direct_t, full_t, overlap_t = relaxed_thresholds(metrics["prop_bigram_count"], op_family)
    if (
        metrics["direct_recall"] >= direct_t
        and metrics["full_recall"] >= full_t
        and metrics["overlap_count"] >= min(overlap_t, metrics["prop_bigram_count"])
    ):
        if _polarity_blocked(prop, op_family, response_text, set(metrics["overlap_set"])):
            return False, "polarity_blocked"
        return True, "relaxed_threshold"

    if op_family is None:
        return False, "relaxed_threshold_fail"

    markers = detector.OPERATOR_CATALOG[op_family]["response_markers"]
    marker_found = False
    for sent in detector._split_sentences(response_text):
        if any(tok in sent for tok in metrics["overlap_set"]) and any(m in sent for m in markers):
            marker_found = True
            break
    if (
        marker_found
        and metrics["direct_recall"] >= 0.10
        and metrics["full_recall"] >= 0.25
        and metrics["overlap_count"] >= 2
    ):
        if _polarity_blocked(prop, op_family, response_text, set(metrics["overlap_set"])):
            return False, "polarity_blocked"
        return True, f"operator_{op_family}"
    return False, "operator_relaxed_fail"


def baseline_check_propositions(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
    relaxed_context: Optional[dict] = None,
) -> Tuple[int, List[int], List[int]]:
    return _check_propositions_mode(
        response_text,
        core_props,
        disqualifying,
        acceptable_variants,
        mode="baseline",
    )


def relaxed_check_propositions(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]] = None,
    acceptable_variants: Optional[List[str]] = None,
    relaxed_context: Optional[dict] = None,
) -> Tuple[int, List[int], List[int]]:
    return _check_propositions_mode(
        response_text,
        core_props,
        disqualifying,
        acceptable_variants,
        mode="relaxed",
    )


def _check_propositions_mode(
    response_text: str,
    core_props: List[str],
    disqualifying: Optional[List[str]],
    acceptable_variants: Optional[List[str]],
    *,
    mode: str,
) -> Tuple[int, List[int], List[int]]:
    if not core_props:
        return 0, [], []

    negation_cues = [
        "ではなく", "ではない", "のではなく", "のではない",
        "じゃない", "誤り", "不適切", "批判", "安易", "短絡",
        "否定", "不要", "不可能",
    ]
    if disqualifying:
        for shortcut in disqualifying:
            if not shortcut or shortcut not in response_text:
                continue
            is_negated = False
            for sent in detector._split_sentences(response_text):
                if shortcut in sent:
                    context = sent.replace(shortcut, "")
                    if any(cue in context for cue in negation_cues):
                        is_negated = True
                        break
            if not is_negated:
                miss_ids = list(range(len(core_props)))
                return 0, [], miss_ids

    variant_bigrams = []
    if acceptable_variants:
        for variant in acceptable_variants:
            if variant and variant in response_text:
                variant_bigrams.append(detector._extract_content_bigrams(variant))

    hit_ids: List[int] = []
    miss_ids: List[int] = []
    for idx, prop in enumerate(core_props):
        metrics = proposition_metrics(response_text, prop, variant_bigrams)
        op = detector.detect_operator(prop)
        if mode == "baseline":
            is_hit = baseline_hit(metrics)
        else:
            is_hit, _ = relaxed_hit(metrics, op.family if op else None, response_text, prop)
        if is_hit:
            hit_ids.append(idx)
        else:
            miss_ids.append(idx)
    return len(hit_ids), hit_ids, miss_ids


@contextmanager
def patched_check_propositions(fn: Callable[..., Tuple[int, List[int], List[int]]]):
    original = detector.check_propositions
    detector.check_propositions = fn
    try:
        yield
    finally:
        detector.check_propositions = original


def run_detect(
    meta_map: Dict[str, dict],
    responses: Dict[str, str],
    *,
    check_fn: Callable[..., Tuple[int, List[int], List[int]]],
) -> Dict[str, dict]:
    results: Dict[str, dict] = {}
    if check_fn is ORIGINAL_CHECK_PROPOSITIONS:
        for qid, meta in meta_map.items():
            response = responses.get(qid)
            if not response:
                continue
            results[qid] = audit_module.audit(qid, response, meta)
        return results

    with patched_check_propositions(check_fn):
        for qid, meta in meta_map.items():
            response = responses.get(qid)
            if not response:
                continue
            results[qid] = audit_module.audit(qid, response, meta)
    return results


def build_candidate_rows(
    meta_map: Dict[str, dict],
    responses: Dict[str, str],
    ha20_map: Dict[str, dict],
    baseline_runs: Dict[str, dict],
    relaxed_runs: Dict[str, dict],
    prop_df: Counter,
) -> List[dict]:
    rows: List[dict] = []
    for qid, meta in meta_map.items():
        response = responses.get(qid)
        if not response:
            continue
        baseline_ev = baseline_runs[qid]["evidence"]
        relaxed_ev = relaxed_runs[qid]["evidence"]
        baseline_new_miss = set(baseline_ev["miss_ids"])
        relaxed_new_hits = set(relaxed_ev["hit_ids"]) - set(baseline_ev["hit_ids"])
        variant_bigrams = [
            detector._extract_content_bigrams(v)
            for v in meta.get("acceptable_variants", [])
            if v and v in response
        ]

        for idx in sorted(relaxed_new_hits):
            if idx not in baseline_new_miss:
                continue
            prop = meta["core_propositions"][idx]
            op = detector.detect_operator(prop)
            metrics = proposition_metrics(response, prop, variant_bigrams)
            direct_t, full_t, overlap_t = relaxed_thresholds(
                metrics["prop_bigram_count"],
                op.family if op else None,
            )
            sentence = best_sentence(response, set(metrics["overlap_set"]))
            chunks = detector._extract_content_chunks(prop)
            matched_chunks = [chunk for chunk in chunks if chunk in sentence]
            rare_matched_chunks = [chunk for chunk in matched_chunks if prop_df[chunk] <= 3]
            generic_matched_chunks = [chunk for chunk in matched_chunks if chunk in GENERIC_CHUNKS]
            rare_overlap = [tok for tok in metrics["direct_overlap_set"] if prop_df[tok] <= 6]
            baseline_state = baseline_runs[qid]["state"]
            relaxed_state = relaxed_runs[qid]["state"]
            fail_max = max(
                baseline_ev["f1_anchor"],
                baseline_ev["f2_unknown"],
                baseline_ev["f3_operator"],
                baseline_ev["f4_premise"],
            )
            rows.append(
                {
                    "qid": qid,
                    "question": meta["question"],
                    "human_score": ha20_map[qid]["human_score"] if qid in ha20_map else "",
                    "low_score_case": qid in LOW_SCORE_IDS,
                    "prop_idx": idx,
                    "proposition": prop,
                    "operator_family": op.family if op else "",
                    "matched_sentence": sentence,
                    "match_reason": "relaxed_new_hit",
                    "baseline_decision": baseline_runs[qid]["policy"]["decision"],
                    "relaxed_decision": relaxed_runs[qid]["policy"]["decision"],
                    "baseline_hits": baseline_ev["propositions_hit"],
                    "relaxed_hits": relaxed_ev["propositions_hit"],
                    "total_props": baseline_ev["propositions_total"],
                    "fail_max": round(fail_max, 4),
                    "baseline_delta_e_A": round(baseline_state["delta_e"], 4),
                    "relaxed_delta_e_A": round(relaxed_state["delta_e"], 4),
                    "prop_bigram_count": metrics["prop_bigram_count"],
                    "direct_overlap_count": metrics["direct_overlap_count"],
                    "overlap_count": metrics["overlap_count"],
                    "direct_recall": round(metrics["direct_recall"], 4),
                    "full_recall": round(metrics["full_recall"], 4),
                    "direct_threshold": direct_t,
                    "full_threshold": full_t,
                    "overlap_threshold": overlap_t,
                    "direct_overlap_set": json.dumps(metrics["direct_overlap_set"], ensure_ascii=False),
                    "overlap_set": json.dumps(metrics["overlap_set"], ensure_ascii=False),
                    "rare_overlap_count": len(rare_overlap),
                    "rare_overlap_set": json.dumps(rare_overlap, ensure_ascii=False),
                    "cascade_duplicate": baseline_ev["hit_sources"].get(str(idx)) == "cascade_rescued"
                    or baseline_ev["hit_sources"].get(idx) == "cascade_rescued",
                    "chunk_match_count": len(matched_chunks),
                    "matched_chunks": json.dumps(matched_chunks, ensure_ascii=False),
                    "rare_chunk_match_count": len(rare_matched_chunks),
                    "rare_matched_chunks": json.dumps(rare_matched_chunks, ensure_ascii=False),
                    "generic_chunk_match_count": len(generic_matched_chunks),
                    "generic_matched_chunks": json.dumps(generic_matched_chunks, ensure_ascii=False),
                    "manual_label": "",
                    "manual_note": "",
                }
            )
    return rows


def filter_candidates(
    candidate_rows: List[dict],
    *,
    delta_max: float,
    rare_overlap_min: int,
    chunk_match_min: int,
    rare_chunk_min: int,
    allow_all_generic_chunks: bool,
) -> set[Tuple[str, int]]:
    accepted = set()
    for row in candidate_rows:
        if (row["qid"], int(row["prop_idx"])) in MANUAL_EXCLUDE:
            continue
        if row["fail_max"] >= 1.0:
            continue
        if row["relaxed_delta_e_A"] > delta_max:
            continue
        if int(row["chunk_match_count"]) < chunk_match_min:
            continue
        if int(row["rare_chunk_match_count"]) < rare_chunk_min:
            continue
        if not allow_all_generic_chunks and (
            int(row["chunk_match_count"]) > 0
            and int(row["generic_chunk_match_count"]) == int(row["chunk_match_count"])
        ):
            continue
        if row["operator_family"]:
            accepted.add((row["qid"], row["prop_idx"]))
            continue
        if row["rare_overlap_count"] >= rare_overlap_min:
            accepted.add((row["qid"], row["prop_idx"]))
    return accepted


def safety_check_propositions_factory(accepted: set[Tuple[str, int]]):
    def safety_check_propositions(
        response_text: str,
        core_props: List[str],
        disqualifying: Optional[List[str]] = None,
        acceptable_variants: Optional[List[str]] = None,
        relaxed_context: Optional[dict] = None,
    ) -> Tuple[int, List[int], List[int]]:
        qid = getattr(safety_check_propositions, "_qid")
        _, baseline_hit_ids, _ = ORIGINAL_CHECK_PROPOSITIONS(
            response_text, core_props, disqualifying, acceptable_variants,
            relaxed_context=relaxed_context,
        )
        _, relaxed_hit_ids, _ = relaxed_check_propositions(
            response_text, core_props, disqualifying, acceptable_variants,
            relaxed_context=relaxed_context,
        )
        hit_ids = sorted(
            set(baseline_hit_ids)
            | {idx for idx in relaxed_hit_ids if (qid, idx) in accepted}
        )
        miss_ids = [idx for idx in range(len(core_props)) if idx not in hit_ids]
        return len(hit_ids), hit_ids, miss_ids

    return safety_check_propositions


@contextmanager
def qid_aware_patch(check_fn_factory, qid: str):
    fn = check_fn_factory
    setattr(fn, "_qid", qid)
    original = detector.check_propositions
    detector.check_propositions = fn
    try:
        yield
    finally:
        detector.check_propositions = original


def run_detect_with_accepts(
    meta_map: Dict[str, dict],
    responses: Dict[str, str],
    accepted: set[Tuple[str, int]],
) -> Dict[str, dict]:
    runs = {}
    for qid, meta in meta_map.items():
        response = responses.get(qid)
        if not response:
            continue
        fn = safety_check_propositions_factory(accepted)
        with qid_aware_patch(fn, qid):
            runs[qid] = audit_module.audit(qid, response, meta)
    return runs


def summarize_runs(runs: Dict[str, dict], ha20_map: Dict[str, dict], gate_map: Dict[str, dict]) -> dict:
    total_hits = sum(r["evidence"]["propositions_hit"] for r in runs.values())
    total_props = sum(r["evidence"]["propositions_total"] for r in runs.values())
    decisions = Counter(r["policy"]["decision"] for r in runs.values())

    ha20_rows = []
    for qid, ann in ha20_map.items():
        if qid not in runs:
            continue
        decision = runs[qid]["policy"]["decision"]
        hit_rate = runs[qid]["evidence"]["propositions_hit"] / max(runs[qid]["evidence"]["propositions_total"], 1)
        s_gate = compute_s_from_gate(gate_map[qid])
        delta_e = compute_delta_e_a(s_gate, hit_rate)
        delta_e_human = compute_delta_e_a(s_gate, ha20_map[qid]["human_hit_rate"])
        ha20_rows.append(
            {
                "id": qid,
                "human_score": ann["human_score"],
                "decision": decision,
                "matched": direction_match(ann["human_score"], decision),
                "system_hit_rate": hit_rate,
                "delta_e_A": delta_e,
                "delta_e_A_human": delta_e_human,
            }
        )

    hs = [row["human_score"] for row in ha20_rows]
    sys_hit = [row["system_hit_rate"] for row in ha20_rows]
    human_hit = [ha20_map[row["id"]]["human_hit_rate"] for row in ha20_rows]
    delta_a_human = [row["delta_e_A_human"] for row in ha20_rows]
    delta_a_system = [row["delta_e_A"] for row in ha20_rows]
    delta_score = [(1.0 - d) ** 0.7 for d in delta_a_human]
    delta_score_system = [(1.0 - d) ** 0.7 for d in delta_a_system]

    return {
        "questions": len(runs),
        "total_hits": total_hits,
        "total_props": total_props,
        "overall_hit_rate": total_hits / max(total_props, 1),
        "accept": decisions["accept"],
        "rewrite": decisions["rewrite"],
        "regenerate": decisions["regenerate"],
        "ha20_direction_matches": sum(int(r["matched"]) for r in ha20_rows),
        "ha20_cases": len(ha20_rows),
        "rho_delta_e_a_human_t07": spearmanr(hs, delta_score),  # invariant under monotonic transform
        "rho_delta_e_a_system_t07": spearmanr(hs, delta_score_system),  # system-dependent
        "rho_hit_rate_human": spearmanr(hs, human_hit),
        "rho_hit_rate_system": spearmanr(hs, sys_hit),
        "ha20_rows": ha20_rows,
    }


def write_csv(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    meta_map = load_question_meta()
    responses = load_responses()
    ha20_map = load_ha20()
    prop_df = build_prop_bigram_df(meta_map)
    gate_map = load_gate_summary()

    baseline_runs = run_detect(meta_map, responses, check_fn=ORIGINAL_CHECK_PROPOSITIONS)
    relaxed_runs = run_detect(meta_map, responses, check_fn=relaxed_check_propositions)

    candidate_rows = build_candidate_rows(
        meta_map,
        responses,
        ha20_map,
        baseline_runs,
        relaxed_runs,
        prop_df,
    )
    candidate_rows.sort(key=lambda r: (r["qid"], r["prop_idx"]))

    # search small safety-valve grid
    search_rows = []
    best = None
    baseline_summary = summarize_runs(baseline_runs, ha20_map, gate_map)
    for delta_max in (0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12):
        for rare_overlap_min in (1, 2):
            for chunk_match_min in (1, 2):
                for rare_chunk_min in (0, 1):
                    for allow_all_generic_chunks in (True, False):
                        accepted = filter_candidates(
                            candidate_rows,
                            delta_max=delta_max,
                            rare_overlap_min=rare_overlap_min,
                            chunk_match_min=chunk_match_min,
                            rare_chunk_min=rare_chunk_min,
                            allow_all_generic_chunks=allow_all_generic_chunks,
                        )
                        filtered_runs = run_detect_with_accepts(meta_map, responses, accepted)
                        summary = summarize_runs(filtered_runs, ha20_map, gate_map)
                        low_score_new_hits = [
                            row for row in candidate_rows
                            if (row["qid"], row["prop_idx"]) in accepted and row["qid"] in LOW_SCORE_IDS
                        ]
                        q015_decision = filtered_runs["q015"]["policy"]["decision"]
                        status = (
                            summary["ha20_direction_matches"] >= 19
                            and q015_decision == "rewrite"
                            and summary["rho_delta_e_a_system_t07"] >= baseline_summary["rho_delta_e_a_system_t07"]
                            and summary["rho_hit_rate_system"] >= baseline_summary["rho_hit_rate_system"]
                            and len(low_score_new_hits) == 0
                        )
                        record = {
                            "delta_max": delta_max,
                            "rare_overlap_min": rare_overlap_min,
                            "chunk_match_min": chunk_match_min,
                            "rare_chunk_min": rare_chunk_min,
                            "allow_all_generic_chunks": allow_all_generic_chunks,
                            "accepted_new_hits": len(accepted),
                            "overall_hit_rate": round(summary["overall_hit_rate"], 6),
                            "ha20_matches": summary["ha20_direction_matches"],
                            "rho_delta_e_a_human_t07": round(summary["rho_delta_e_a_human_t07"], 4),
                            "rho_delta_e_a_system_t07": round(summary["rho_delta_e_a_system_t07"], 4),
                            "rho_hit_rate_system": round(summary["rho_hit_rate_system"], 4),
                            "q015_decision": q015_decision,
                            "low_score_new_hits": len(low_score_new_hits),
                            "status": "GO" if status else "NO-GO",
                        }
                        search_rows.append(record)
                        if status and (
                            best is None
                            or (
                                record["delta_max"],
                                0 if not record["allow_all_generic_chunks"] else 1,
                                -record["accepted_new_hits"],
                            )
                            < (
                                best["record"]["delta_max"],
                                0 if not best["record"]["allow_all_generic_chunks"] else 1,
                                -best["record"]["accepted_new_hits"],
                            )
                        ):
                            best = {
                                "record": record,
                                "accepted": accepted,
                                "summary": summary,
                                "runs": filtered_runs,
                                "low_score_new_hits": low_score_new_hits,
                            }

    if best is None:
        fallback_record = dict(search_rows[0]) if search_rows else {}
        fallback_record["status"] = "NO-GO (fallback: no GO candidate found)"
        best = {
            "record": fallback_record,
            "accepted": set(),
            "summary": baseline_summary,
            "runs": baseline_runs,
            "low_score_new_hits": [],
            "is_fallback": True,
        }

    accepted_rows = [
        row for row in candidate_rows
        if (row["qid"], row["prop_idx"]) in best["accepted"]
    ]
    rejected_rows = [
        row for row in candidate_rows
        if (row["qid"], row["prop_idx"]) not in best["accepted"]
    ]

    accepted_rows.sort(key=lambda r: (r["qid"], r["prop_idx"]))
    rejected_rows.sort(key=lambda r: (r["qid"], r["prop_idx"]))

    baseline_ha20 = {r["id"]: r for r in baseline_summary["ha20_rows"]}
    best_ha20 = {r["id"]: r for r in best["summary"]["ha20_rows"]}
    ha20_diff = []
    for qid, row in baseline_ha20.items():
        other = best_ha20[qid]
        if row["decision"] != other["decision"] or row["system_hit_rate"] != other["system_hit_rate"]:
            ha20_diff.append(
                {
                    "id": qid,
                    "human_score": ha20_map[qid]["human_score"],
                    "baseline_decision": row["decision"],
                    "filtered_decision": other["decision"],
                    "baseline_hit_rate": row["system_hit_rate"],
                    "filtered_hit_rate": other["system_hit_rate"],
                    "baseline_delta_e_A": row["delta_e_A"],
                    "filtered_delta_e_A": other["delta_e_A"],
                    "baseline_match": row["matched"],
                    "filtered_match": other["matched"],
                }
            )

    summary = {
        "baseline": {
            "overall_hit_rate": baseline_summary["overall_hit_rate"],
            "ha20_direction_matches": baseline_summary["ha20_direction_matches"],
            "rho_delta_e_a_human_t07": baseline_summary["rho_delta_e_a_human_t07"],
            "rho_delta_e_a_system_t07": baseline_summary["rho_delta_e_a_system_t07"],
            "rho_hit_rate_human": baseline_summary["rho_hit_rate_human"],
            "rho_hit_rate_system": baseline_summary["rho_hit_rate_system"],
        },
        "relaxed_candidates": {
            "candidate_rows": len(candidate_rows),
            "low_score_candidates": sum(1 for row in candidate_rows if row["qid"] in LOW_SCORE_IDS),
            "cascade_duplicates": sum(1 for row in candidate_rows if row["cascade_duplicate"]),
        },
        "best_filter": best["record"],
        "best_filter_is_fallback": best.get("is_fallback", False),
        "filtered": {
            "overall_hit_rate": best["summary"]["overall_hit_rate"],
            "ha20_direction_matches": best["summary"]["ha20_direction_matches"],
            "rho_delta_e_a_human_t07": best["summary"]["rho_delta_e_a_human_t07"],
            "rho_delta_e_a_system_t07": best["summary"]["rho_delta_e_a_system_t07"],
            "rho_hit_rate_human": best["summary"]["rho_hit_rate_human"],
            "rho_hit_rate_system": best["summary"]["rho_hit_rate_system"],
        },
        "acceptance_checks": {
            "ha20_at_least_19": best["summary"]["ha20_direction_matches"] >= 19,
            "q015_rewrite": best["runs"]["q015"]["policy"]["decision"] == "rewrite",
            "rho_delta_e_system_not_worse": best["summary"]["rho_delta_e_a_system_t07"] >= baseline_summary["rho_delta_e_a_system_t07"],
            "rho_system_not_worse": best["summary"]["rho_hit_rate_system"] >= baseline_summary["rho_hit_rate_system"],
            "low_score_new_hits_zero": len(best["low_score_new_hits"]) == 0,
        },
    }

    write_csv(
        OUT_DIR / "proposition_hit_candidates_all.csv",
        candidate_rows,
        list(candidate_rows[0].keys()) if candidate_rows else [],
    )
    if accepted_rows:
        write_csv(
            OUT_DIR / "proposition_hit_candidates_accepted.csv",
            accepted_rows,
            list(accepted_rows[0].keys()),
        )
    if rejected_rows:
        write_csv(
            OUT_DIR / "proposition_hit_candidates_rejected.csv",
            rejected_rows,
            list(rejected_rows[0].keys()),
        )
    if search_rows:
        write_csv(
            OUT_DIR / "proposition_hit_filter_grid.csv",
            search_rows,
            list(search_rows[0].keys()),
        )
    if ha20_diff:
        write_csv(
            OUT_DIR / "proposition_hit_ha20_diff.csv",
            ha20_diff,
            list(ha20_diff[0].keys()),
        )

    (OUT_DIR / "proposition_hit_experiment_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"all_candidates_csv={OUT_DIR / 'proposition_hit_candidates_all.csv'}")
    print(f"accepted_candidates_csv={OUT_DIR / 'proposition_hit_candidates_accepted.csv'}")
    print(f"rejected_candidates_csv={OUT_DIR / 'proposition_hit_candidates_rejected.csv'}")
    print(f"filter_grid_csv={OUT_DIR / 'proposition_hit_filter_grid.csv'}")
    print(f"ha20_diff_csv={OUT_DIR / 'proposition_hit_ha20_diff.csv'}")


if __name__ == "__main__":
    main()
