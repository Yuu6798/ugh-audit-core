"""Diagnostic: check_propositions の recall 分布を確認し、
   閾値引き下げの影響をシミュレーションする。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    _extract_content_bigrams,
    _expand_with_synonyms,
    _MIN_OVERLAP,
    _split_sentences,
)


def load_data():
    base = Path(__file__).resolve().parent.parent / "data"
    questions = {}
    with open(base / "question_sets/ugh-audit-100q-v3-1.json.txtl.txt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            questions[obj["id"]] = obj

    responses = {}
    with open(base / "phase_c_scored_v1_t0_only.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            responses[obj["id"]] = obj
    return questions, responses


def check_propositions_debug(response_text, core_props, disqualifying=None, acceptable_variants=None):
    """check_propositions と同じロジックだが、各命題の recall 値を返す"""
    _NEGATION_CUES = ["ではなく", "ではない", "のではなく", "誤り", "不適切",
                      "批判", "安易", "短絡"]
    if not core_props:
        return []

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
                return [{"prop_idx": i, "disqualified": True} for i in range(len(core_props))]

    resp_bigrams = _extract_content_bigrams(response_text)

    all_variant_bigrams = []
    if acceptable_variants:
        for variant in acceptable_variants:
            if variant and variant in response_text:
                all_variant_bigrams.append(_extract_content_bigrams(variant))

    results = []
    for i, prop in enumerate(core_props):
        prop_bigrams = _extract_content_bigrams(prop)
        if not prop_bigrams:
            results.append({"prop_idx": i, "empty_bigrams": True})
            continue

        expanded = _expand_with_synonyms(prop_bigrams)
        for vbg in all_variant_bigrams:
            common = vbg & prop_bigrams
            if len(common) >= max(2, len(prop_bigrams) * 0.3):
                expanded |= vbg

        overlap_set = expanded & resp_bigrams
        overlap_count = len(overlap_set)
        direct_overlap = len(prop_bigrams & resp_bigrams)
        direct_recall = direct_overlap / len(prop_bigrams)
        full_recall = overlap_count / len(prop_bigrams)
        min_required = min(_MIN_OVERLAP, len(prop_bigrams))

        # Jaccard 計算
        jaccard_direct = len(prop_bigrams & resp_bigrams) / len(prop_bigrams | resp_bigrams) if (prop_bigrams | resp_bigrams) else 0
        jaccard_expanded = len(expanded & resp_bigrams) / len(expanded | resp_bigrams) if (expanded | resp_bigrams) else 0

        hit = (direct_recall >= 0.15 and full_recall >= 0.35 and overlap_count >= min_required)

        results.append({
            "prop_idx": i,
            "prop_text": prop[:60],
            "n_prop_bigrams": len(prop_bigrams),
            "n_expanded": len(expanded),
            "direct_overlap": direct_overlap,
            "full_overlap": overlap_count,
            "direct_recall": round(direct_recall, 4),
            "full_recall": round(full_recall, 4),
            "jaccard_direct": round(jaccard_direct, 4),
            "jaccard_expanded": round(jaccard_expanded, 4),
            "min_required": min_required,
            "hit": hit,
        })
    return results


def main():
    questions, responses = load_data()

    all_results = []
    for qid in sorted(responses.keys()):
        q_meta = questions.get(qid, responses[qid])
        resp_text = responses[qid].get("response", "")
        core_props = q_meta.get("core_propositions", [])
        disqualifying = q_meta.get("disqualifying_shortcuts", [])
        acceptable_variants = q_meta.get("acceptable_variants", [])

        props_debug = check_propositions_debug(resp_text, core_props, disqualifying, acceptable_variants)
        for pd in props_debug:
            pd["qid"] = qid
            all_results.append(pd)

    # Summary
    hits = [r for r in all_results if r.get("hit")]
    misses = [r for r in all_results if r.get("hit") is False]
    disqualified = [r for r in all_results if r.get("disqualified")]

    print(f"Total propositions: {len(all_results)}")
    print(f"Hits: {len(hits)}")
    print(f"Misses: {len(misses)}")
    print(f"Disqualified: {len(disqualified)}")
    print(f"Hit rate: {len(hits)/len(all_results):.3f}")

    # Show miss distribution near boundary
    print("\n=== MISS distribution (sorted by full_recall desc) ===")
    near_misses = sorted(
        [r for r in misses if "full_recall" in r],
        key=lambda x: x["full_recall"],
        reverse=True,
    )
    print(f"{'qid':>5} {'idx':>3} {'n_bg':>4} {'d_ovlp':>6} {'f_ovlp':>6} {'d_rec':>6} {'f_rec':>6} {'j_dir':>6} {'j_exp':>6} {'min_req':>7} prop_text")
    for r in near_misses[:40]:
        print(f"{r['qid']:>5} {r['prop_idx']:>3} {r['n_prop_bigrams']:>4} {r['direct_overlap']:>6} {r['full_overlap']:>6} {r['direct_recall']:>6.3f} {r['full_recall']:>6.3f} {r['jaccard_direct']:>6.4f} {r['jaccard_expanded']:>6.4f} {r['min_required']:>7} {r['prop_text']}")

    # Show which threshold matters for each miss
    print("\n=== Binding constraint analysis ===")
    binding_full_recall = 0
    binding_direct_recall = 0
    binding_overlap = 0
    binding_multiple = 0
    for r in misses:
        if "full_recall" not in r:
            continue
        fails = []
        if r["direct_recall"] < 0.15:
            fails.append("direct_recall")
        if r["full_recall"] < 0.35:
            fails.append("full_recall")
        if r["full_overlap"] < r["min_required"]:
            fails.append("min_overlap")
        if len(fails) == 1:
            if fails[0] == "full_recall":
                binding_full_recall += 1
            elif fails[0] == "direct_recall":
                binding_direct_recall += 1
            else:
                binding_overlap += 1
        else:
            binding_multiple += 1
    print(f"  Only full_recall too low: {binding_full_recall}")
    print(f"  Only direct_recall too low: {binding_direct_recall}")
    print(f"  Only min_overlap too low: {binding_overlap}")
    print(f"  Multiple constraints: {binding_multiple}")

    # Check Jaccard distribution for misses
    print("\n=== Jaccard (expanded) distribution for misses ===")
    jaccard_values = sorted([r.get("jaccard_expanded", 0) for r in misses if "jaccard_expanded" in r], reverse=True)
    for j_thresh in [0.12, 0.11, 0.10, 0.09, 0.08, 0.07, 0.06, 0.05]:
        count_above = sum(1 for j in jaccard_values if j >= j_thresh)
        print(f"  >= {j_thresh}: {count_above} misses would flip")


if __name__ == "__main__":
    main()
