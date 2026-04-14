"""grv v1.3 受け入れ試験 — V-1〜V-6 + 重み探索"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter

sys.path.insert(0, ".")

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


def run_grv(w_d=0.60, w_s=0.10):
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
            w_collapse_v2=0.0,
        )
        if r is None:
            continue
        hs = round(float(ha48[qid]["O"])) if ha48[qid]["O"] else None
        results.append({
            "id": qid, "grv": r.grv, "drift": r.drift, "dispersion": r.dispersion,
            "collapse_v2": r.collapse_v2, "tag": r.grv_tag, "n_sent": r.n_sentences,
            "n_props": r.n_propositions, "collapse_v2_applicable": r.collapse_v2_applicable, "O": hs,
        })
    return results


def spearman_rho(xs, ys):
    """簡易 Spearman ρ"""
    from scipy.stats import spearmanr
    r, _ = spearmanr(xs, ys)
    return r


print("=" * 60)
print("grv v1.3 Acceptance Test (HA48)")
print("=" * 60)

# --- 確定重み (collapse 除外済み) ---
print("\n--- Weight: w_d=0.60 w_s=0.10 (collapse excluded) ---")
results = run_grv()
grvs_all = [r["grv"] for r in results]
print(f"  n={len(results)}  mean={statistics.mean(grvs_all):.4f}  std={statistics.stdev(grvs_all):.4f}")

# --- Summary ---
grv_vals = [r["grv"] for r in results]
drift_vals = [r["drift"] for r in results]
disp_vals = [r["dispersion"] for r in results]
col_vals = [r["collapse"] for r in results]

print("\n--- Component distributions ---")
print(f"grv:        mean={statistics.mean(grv_vals):.4f}  std={statistics.stdev(grv_vals):.4f}"
      f"  min={min(grv_vals):.4f}  max={max(grv_vals):.4f}")
print(f"drift:      mean={statistics.mean(drift_vals):.4f}  std={statistics.stdev(drift_vals):.4f}"
      f"  min={min(drift_vals):.4f}  max={max(drift_vals):.4f}")
print(f"dispersion: mean={statistics.mean(disp_vals):.4f}  std={statistics.stdev(disp_vals):.4f}"
      f"  min={min(disp_vals):.4f}  max={max(disp_vals):.4f}")
print(f"collapse:   mean={statistics.mean(col_vals):.4f}  std={statistics.stdev(col_vals):.4f}"
      f"  min={min(col_vals):.4f}  max={max(col_vals):.4f}")

tags = Counter(r["tag"] for r in results)
print(f"\nTag distribution: {dict(tags)}")

# --- V-1: Non-degeneracy ---
print("\n--- V-1: Non-degeneracy ---")
sigma = statistics.stdev(grv_vals)
tag_count = len(tags)
v1_sigma = sigma > 0.05
v1_tags = tag_count >= 2
print(f"sigma(grv) = {sigma:.4f}  {'PASS' if v1_sigma else 'FAIL'} (> 0.05)")
print(f"tag spread = {tag_count} tags  {'PASS' if v1_tags else 'FAIL'} (>= 2)")

# --- V-2: Quasi-positive elevation ---
print("\n--- V-2: Quasi-positive elevation ---")
quasi_pos = {"q067", "q099", "q037", "q086", "q093"}
control = {"q009", "q063", "q083", "q075", "q088"}
qp_grv = [r["grv"] for r in results if r["id"] in quasi_pos]
ctrl_grv = [r["grv"] for r in results if r["id"] in control]
qp_col = [r["collapse"] for r in results if r["id"] in quasi_pos]
ctrl_col = [r["collapse"] for r in results if r["id"] in control]
v2_grv = statistics.mean(qp_grv) > statistics.mean(ctrl_grv) if qp_grv and ctrl_grv else False
v2_col = statistics.mean(qp_col) > statistics.mean(ctrl_col) if qp_col and ctrl_col else False
print(f"quasi-pos grv mean={statistics.mean(qp_grv):.4f}  control={statistics.mean(ctrl_grv):.4f}"
      f"  {'PASS' if v2_grv else 'FAIL'}")
print(f"quasi-pos col mean={statistics.mean(qp_col):.4f}  control={statistics.mean(ctrl_col):.4f}"
      f"  {'PASS' if v2_col else 'FAIL'}")

# --- V-3: Short-answer false positive ---
print("\n--- V-3: Short-answer false positive ---")
short_high = [r for r in results if r["O"] is not None and r["O"] >= 4 and r["n_sent"] <= 3]
fp_high = [r for r in short_high if r["tag"] == "high_gravity"]
v3 = len(fp_high) == 0
print(f"high-score short answers: {len(short_high)}, high_gravity tags: {len(fp_high)}"
      f"  {'PASS' if v3 else 'FAIL'}")

# --- V-4: Incremental contribution (N/A — collapse excluded) ---
print("\n--- V-4: Incremental contribution ---")
scores = [r["O"] for r in results if r["O"] is not None]
g_paired = [r["grv"] for r in results if r["O"] is not None]
rho_grv = spearman_rho(g_paired, scores)
print(f"N/A (collapse excluded from composite; rho(grv)={rho_grv:.4f})")
print("collapse v2 redesign needed for V-4 to apply")

# --- V-5: Direction ---
print("\n--- V-5: Direction ---")
rho_full = spearman_rho(g_paired, scores)
v5 = rho_full < 0
print(f"rho(grv, human_score) = {rho_full:.4f}  {'PASS' if v5 else 'FAIL'} (should be < 0)")

# --- V-6: Component non-degeneracy ---
print("\n--- V-6: Component non-degeneracy ---")
v6_drift = statistics.stdev(drift_vals) > 0.03
v6_disp = statistics.stdev(disp_vals) > 0.03
v6_col = statistics.stdev(col_vals) > 0.05
print(f"drift std={statistics.stdev(drift_vals):.4f}  {'PASS' if v6_drift else 'FAIL'} (> 0.03)")
print(f"disp  std={statistics.stdev(disp_vals):.4f}  {'PASS' if v6_disp else 'FAIL'} (> 0.03)")
print(f"col   std={statistics.stdev(col_vals):.4f}  {'PASS' if v6_col else 'FAIL'} (> 0.05)")

# --- Summary ---
print("\n" + "=" * 60)
all_pass = all([v1_sigma, v1_tags, v2_grv, v3, v5, v6_drift, v6_disp])
# Note: v6_col と v2_col は collapse が診断出力のみのため informational
print(f"Overall: {'ALL PASS' if all_pass else 'SOME FAIL'}")
print("Weights: w_d=0.60 w_s=0.10 (collapse excluded)")
print("tau=0.1")
print(f"rho(grv, human_score) = {rho_full:.4f}")

# Per-question
print("\n--- Per-question (sorted by grv desc) ---")
print(f"{'id':>6}  {'grv':>6}  {'drift':>6}  {'disp':>6}  {'col':>6}  {'tag':<14}  {'O':>2}")
for r in sorted(results, key=lambda x: x["grv"], reverse=True):
    o_str = str(r["O"]) if r["O"] is not None else "-"
    print(f"{r['id']:>6}  {r['grv']:6.4f}  {r['drift']:6.4f}  {r['dispersion']:6.4f}"
          f"  {r['collapse']:6.4f}  {r['tag']:<14}  {o_str:>2}")
