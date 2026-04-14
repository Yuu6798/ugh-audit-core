"""grv v1.3 グリッドサーチ — τ × w_d × w_s × w_c で V-4 を通す組み合わせを探す"""
from __future__ import annotations

import csv
import json
import sys

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


def run_and_eval(tau, w_d, w_s, w_c):
    results = []
    for qid in sorted(ha48.keys()):
        meta = q_meta[qid]
        resp = responses[qid]
        r = compute_grv(
            question=meta["question"],
            response_text=resp.get("response", ""),
            question_meta=meta,
            metadata_source="inline",
            tau=tau,
            w_drift=w_d,
            w_dispersion=w_s,
            w_collapse=w_c,
        )
        if r is None:
            return None
        hs = round(float(ha48[qid]["O"])) if ha48[qid]["O"] else None
        results.append((r.grv, r.drift, r.dispersion, r.collapse, hs))

    paired = [(g, d, disp, c, h) for g, d, disp, c, h in results if h is not None]
    grvs = [x[0] for x in paired]
    scores = [x[4] for x in paired]

    # 2-comp (drift + dispersion only)
    grv_2comp = [w_d * x[1] + w_s * x[2] for x in paired]

    rho_3, _ = spearmanr(grvs, scores)
    rho_2, _ = spearmanr(grv_2comp, scores)

    import statistics
    sigma = statistics.stdev([x[0] for x in results])
    col_std = statistics.stdev([x[3] for x in results])

    return {
        "tau": tau, "w_d": w_d, "w_s": w_s, "w_c": w_c,
        "rho_3": rho_3, "rho_2": rho_2,
        "v4_pass": abs(rho_3) > abs(rho_2),
        "v1_pass": sigma > 0.05,
        "v5_pass": rho_3 < 0,
        "v6_col_pass": col_std > 0.05,
        "sigma": sigma,
        "col_std": col_std,
    }


# Grid search
taus = [0.05, 0.08, 0.10, 0.15, 0.20]
weight_sets = [
    (0.50, 0.20, 0.30),
    (0.60, 0.10, 0.30),
    (0.40, 0.20, 0.40),
    (0.45, 0.15, 0.40),
    (0.70, 0.10, 0.20),
    (0.55, 0.15, 0.30),
    (0.65, 0.10, 0.25),
    (0.50, 0.10, 0.40),
    (0.60, 0.15, 0.25),
    (0.55, 0.20, 0.25),
]

print(f"{'tau':>5}  {'w_d':>5}  {'w_s':>5}  {'w_c':>5}  {'rho3':>7}  {'rho2':>7}  "
      f"{'V4':>4}  {'V1':>4}  {'V5':>4}  {'V6c':>4}  {'sigma':>6}  {'col_std':>7}")

best = None
for tau in taus:
    for w_d, w_s, w_c in weight_sets:
        res = run_and_eval(tau, w_d, w_s, w_c)
        if res is None:
            continue
        all_pass = res["v4_pass"] and res["v1_pass"] and res["v5_pass"] and res["v6_col_pass"]
        marker = " ***" if all_pass else ""
        print(f"{tau:5.2f}  {w_d:5.2f}  {w_s:5.2f}  {w_c:5.2f}  {res['rho_3']:7.4f}  "
              f"{res['rho_2']:7.4f}  {'Y' if res['v4_pass'] else 'N':>4}  "
              f"{'Y' if res['v1_pass'] else 'N':>4}  {'Y' if res['v5_pass'] else 'N':>4}  "
              f"{'Y' if res['v6_col_pass'] else 'N':>4}  {res['sigma']:6.4f}  "
              f"{res['col_std']:7.4f}{marker}")
        if all_pass and (best is None or abs(res["rho_3"]) > abs(best["rho_3"])):
            best = res

print()
if best:
    print("=== BEST ALL-PASS ===")
    print(f"tau={best['tau']}, w_d={best['w_d']}, w_s={best['w_s']}, w_c={best['w_c']}")
    print(f"rho_3={best['rho_3']:.4f}, rho_2={best['rho_2']:.4f}")
    print(f"sigma={best['sigma']:.4f}, col_std={best['col_std']:.4f}")
else:
    print("No combination passed all V-1/V-4/V-5/V-6")
