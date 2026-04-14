"""HA48 grv 非退化性チェック — Phase 2 V-1〜V-6 (v1.3)"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter

sys.path.insert(0, ".")

from grv_calculator import compute_grv

# Load data
ha48 = {}
with open("data/human_annotation_48/annotation_48_merged.csv", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        ha48[row["id"]] = row

q_meta = {}
with open("data/question_sets/ugh-audit-100q-v3-1.json.txtl.txt", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec["id"] in ha48:
            q_meta[rec["id"]] = rec

responses = {}
with open("data/phase_c_scored_v1_t0_only.jsonl", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        qid = rec.get("question_id") or rec.get("id")
        if qid in ha48:
            responses[qid] = rec

# Run grv
results = []
for qid in sorted(ha48.keys()):
    meta = q_meta[qid]
    resp = responses[qid]
    r = compute_grv(
        question=meta["question"],
        response_text=resp.get("response", ""),
        question_meta=meta,
        metadata_source="inline",
    )
    if r is None:
        print(f"{qid}: SBert unavailable")
        continue
    human_score = round(float(ha48[qid]["O"])) if ha48[qid]["O"] else None
    results.append({
        "id": qid,
        "grv": r.grv,
        "drift": r.drift,
        "dispersion": r.dispersion,
        "collapse": r.collapse,
        "tag": r.grv_tag,
        "n_sent": r.n_sentences,
        "n_props": r.n_propositions,
        "collapse_applicable": r.collapse_applicable,
        "O": human_score,
    })

if not results:
    print("ERROR: No results computed")
    sys.exit(1)

# Summary
grv_vals = [r["grv"] for r in results]
drift_vals = [r["drift"] for r in results]
disp_vals = [r["dispersion"] for r in results]
col_vals = [r["collapse"] for r in results]

print(f"=== grv Non-Degeneracy Check (HA48, n={len(results)}) ===")
print(f"grv:        mean={statistics.mean(grv_vals):.4f}  std={statistics.stdev(grv_vals):.4f}"
      f"  min={min(grv_vals):.4f}  max={max(grv_vals):.4f}")
print(f"drift:      mean={statistics.mean(drift_vals):.4f}  std={statistics.stdev(drift_vals):.4f}"
      f"  min={min(drift_vals):.4f}  max={max(drift_vals):.4f}")
print(f"dispersion: mean={statistics.mean(disp_vals):.4f}  std={statistics.stdev(disp_vals):.4f}"
      f"  min={min(disp_vals):.4f}  max={max(disp_vals):.4f}")
print(f"collapse:   mean={statistics.mean(col_vals):.4f}  std={statistics.stdev(col_vals):.4f}"
      f"  min={min(col_vals):.4f}  max={max(col_vals):.4f}")

# Tag distribution
tags = Counter(r["tag"] for r in results)
print(f"\nTag distribution: {dict(tags)}")

# collapse_applicable
applicable = sum(1 for r in results if r["collapse_applicable"])
print(f"collapse_applicable: {applicable}/{len(results)}")

# V-1 check
sigma = statistics.stdev(grv_vals)
verdict = "PASS" if sigma > 0.05 else "FAIL"
print("\n=== V-1: Non-degeneracy ===")
print(f"sigma(grv) = {sigma:.4f}  {verdict} (threshold: > 0.05)")

# Per-question detail
print("\n=== Per-question results ===")
print(f"{'id':>6}  {'grv':>6}  {'drift':>6}  {'disp':>6}  {'col':>6}  {'tag':<14}  {'O':>2}")
for r in sorted(results, key=lambda x: x["grv"], reverse=True):
    o_str = str(r["O"]) if r["O"] is not None else "-"
    print(f"{r['id']:>6}  {r['grv']:6.4f}  {r['drift']:6.4f}  {r['dispersion']:6.4f}"
          f"  {r['collapse']:6.4f}  {r['tag']:<14}  {o_str:>2}")
