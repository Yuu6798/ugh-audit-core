"""grv v1.4 受け入れ試験 — V-1〜V-6 + 重み校正 + cover_soft/wash_index 分析"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter

sys.path.insert(0, ".")

from scipy.stats import spearmanr

from grv_calculator import compute_grv

# --- Data loading ---
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


def run_grv(w_d, w_s, w_c):
    results = []
    for qid in sorted(ha48.keys()):
        meta = q_meta[qid]
        resp = responses[qid]
        r = compute_grv(
            question=meta["question"],
            response_text=resp.get("response", ""),
            question_meta=meta,
            metadata_source="inline",
            w_drift=w_d,
            w_dispersion=w_s,
            w_collapse_v2=w_c,
        )
        if r is None:
            continue
        hs = round(float(ha48[qid]["O"])) if ha48[qid]["O"] else None
        results.append({
            "id": qid, "grv": r.grv, "drift": r.drift, "dispersion": r.dispersion,
            "collapse_v2": r.collapse_v2, "cover_soft": r.cover_soft,
            "wash_index": r.wash_index, "wash_index_c": r.wash_index_c,
            "tag": r.grv_tag, "n_sent": r.n_sentences, "n_props": r.n_propositions,
            "collapse_v2_applicable": r.collapse_v2_applicable, "O": hs,
        })
    return results


print("=" * 60)
print("grv v1.4 Acceptance Test (HA48)")
print("=" * 60)

# --- Task 4: 重み校正 ---
print("\n--- Task 4: Weight calibration ---")
weight_candidates = [
    (0.60, 0.10, 0.00, "2comp baseline"),
    (0.50, 0.10, 0.40, "cand1"),
    (0.50, 0.20, 0.30, "cand2"),
    (0.40, 0.10, 0.50, "cand3"),
    (0.45, 0.15, 0.40, "cand4"),
    (0.60, 0.10, 0.30, "cand5"),
]

best_rho = 0
best_label = None
best_results = None
baseline_rho = None

for w_d, w_s, w_c, label in weight_candidates:
    res = run_grv(w_d, w_s, w_c)
    paired = [(r["grv"], r["O"]) for r in res if r["O"] is not None]
    grvs = [p[0] for p in paired]
    scores = [p[1] for p in paired]
    rho, _ = spearmanr(grvs, scores)
    sigma = statistics.stdev([r["grv"] for r in res])
    print(f"  {label:>20}: w_d={w_d:.2f} w_s={w_s:.2f} w_c={w_c:.2f}  "
          f"rho={rho:.4f}  sigma={sigma:.4f}")
    if label == "2comp baseline":
        baseline_rho = rho
    if abs(rho) > abs(best_rho):
        best_rho = rho
        best_label = label
        best_results = res
        best_weights = (w_d, w_s, w_c)

print(f"  ** Best: {best_label} (rho={best_rho:.4f})")

# Check incremental contribution
if best_weights[2] > 0 and abs(best_rho) > abs(baseline_rho):
    print(f"  V-4: collapse_v2 incremental POSITIVE (baseline rho={baseline_rho:.4f})")
    use_collapse = True
else:
    print("  V-4: collapse_v2 incremental NEGATIVE or ZERO — using 2comp")
    use_collapse = False
    best_results = run_grv(0.60, 0.10, 0.00)
    best_weights = (0.60, 0.10, 0.00)

results = best_results

# --- Summary ---
def stats(vals):
    return (statistics.mean(vals), statistics.stdev(vals), min(vals), max(vals))

fields = {
    "grv": [r["grv"] for r in results],
    "drift": [r["drift"] for r in results],
    "dispersion": [r["dispersion"] for r in results],
    "collapse_v2": [r["collapse_v2"] for r in results],
    "cover_soft": [r["cover_soft"] for r in results],
    "wash_index": [r["wash_index"] for r in results],
}

print("\n--- Component distributions ---")
for name, vals in fields.items():
    m, s, mn, mx = stats(vals)
    print(f"{name:>12}: mean={m:.4f}  std={s:.4f}  min={mn:.4f}  max={mx:.4f}")

tags = Counter(r["tag"] for r in results)
print(f"\nTag distribution: {dict(tags)}")

# --- V-1 ---
print("\n--- V-1: Non-degeneracy ---")
sigma = statistics.stdev(fields["grv"])
tag_count = len(tags)
v1_sigma = sigma > 0.05
v1_tags = tag_count >= 2
print(f"sigma(grv) = {sigma:.4f}  {'PASS' if v1_sigma else 'FAIL'}")
print(f"tag spread = {tag_count}  {'PASS' if v1_tags else 'FAIL'}")

# --- V-2 ---
print("\n--- V-2: Quasi-positive elevation ---")
quasi_pos = {"q067", "q099", "q037", "q086", "q093"}
control = {"q009", "q063", "q083", "q075", "q088"}

for metric in ["grv", "collapse_v2", "wash_index"]:
    qp = [r[metric] for r in results if r["id"] in quasi_pos]
    ct = [r[metric] for r in results if r["id"] in control]
    qp_m = statistics.mean(qp) if qp else 0
    ct_m = statistics.mean(ct) if ct else 0
    ok = qp_m > ct_m
    print(f"  {metric:>12}: quasi-pos={qp_m:.4f}  control={ct_m:.4f}  {'PASS' if ok else 'FAIL'}")

# --- V-3 ---
print("\n--- V-3: Short-answer false positive ---")
short_high = [r for r in results if r["O"] is not None and r["O"] >= 4 and r["n_sent"] <= 3]
fp = [r for r in short_high if r["tag"] == "high_gravity"]
v3 = len(fp) == 0
print(f"high-score short: {len(short_high)}, high_gravity: {len(fp)}  {'PASS' if v3 else 'FAIL'}")

# --- V-5 ---
print("\n--- V-5: Direction ---")
paired = [(r["grv"], r["O"]) for r in results if r["O"] is not None]
rho_full, _ = spearmanr([p[0] for p in paired], [p[1] for p in paired])
v5 = rho_full < 0
print(f"rho(grv, human_score) = {rho_full:.4f}  {'PASS' if v5 else 'FAIL'}")

# --- V-6 ---
print("\n--- V-6: Component non-degeneracy ---")
v6_d = statistics.stdev(fields["drift"]) > 0.03
v6_s = statistics.stdev(fields["dispersion"]) > 0.03
v6_c = statistics.stdev(fields["collapse_v2"]) > 0.05
print(f"drift std={statistics.stdev(fields['drift']):.4f}  {'PASS' if v6_d else 'FAIL'}")
print(f"disp  std={statistics.stdev(fields['dispersion']):.4f}  {'PASS' if v6_s else 'FAIL'}")
print(f"col_v2 std={statistics.stdev(fields['collapse_v2']):.4f}  {'PASS' if v6_c else 'FAIL'}")

# --- cover_soft vs C correlation ---
print("\n--- cover_soft analysis ---")
cs_vals = [r["cover_soft"] for r in results if r["O"] is not None]
hs_vals = [r["O"] for r in results if r["O"] is not None]
rho_cs, _ = spearmanr(cs_vals, hs_vals)
print(f"rho(cover_soft, human_score) = {rho_cs:.4f}")

# --- wash_index ---
print("\n--- wash_index analysis ---")
wi_vals = [r["wash_index"] for r in results if r["O"] is not None]
rho_wi, _ = spearmanr(wi_vals, hs_vals)
print(f"rho(wash_index, human_score) = {rho_wi:.4f}")

# --- Overall ---
print("\n" + "=" * 60)
print(f"Weights: w_d={best_weights[0]:.2f} w_s={best_weights[1]:.2f} w_c={best_weights[2]:.2f}")
print(f"collapse_v2 in composite: {use_collapse}")
print(f"rho(grv, human_score) = {rho_full:.4f}")

# --- Per-question ---
print("\n--- Per-question (sorted by grv desc) ---")
print(f"{'id':>6}  {'grv':>6}  {'drift':>6}  {'disp':>6}  {'col2':>6}  "
      f"{'csoft':>6}  {'wash':>6}  {'tag':<14}  {'O':>2}")
for r in sorted(results, key=lambda x: x["grv"], reverse=True):
    o_str = str(r["O"]) if r["O"] is not None else "-"
    print(f"{r['id']:>6}  {r['grv']:6.4f}  {r['drift']:6.4f}  {r['dispersion']:6.4f}"
          f"  {r['collapse_v2']:6.4f}  {r['cover_soft']:6.4f}  {r['wash_index']:6.4f}"
          f"  {r['tag']:<14}  {o_str:>2}")
