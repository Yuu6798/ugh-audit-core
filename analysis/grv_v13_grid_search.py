"""grv v1.3 グリッドサーチ — τ × w_d × w_s で最良 ρ を探す

注: collapse は合成値から除外済み (V-4 は N/A)。
本スクリプトは drift+dispersion の重み配分と τ の最適化に使用する。
w_c 列は collapse 診断の τ 依存性の記録用に残している。"""
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
            # w_collapse removed — collapse excluded from composite
        )
        if r is None:
            return None
        hs = round(float(ha48[qid]["O"])) if ha48[qid]["O"] else None
        results.append((r.grv, r.drift, r.dispersion, r.collapse, hs))

    paired = [(g, d, disp, c, h) for g, d, disp, c, h in results if h is not None]
    grvs = [x[0] for x in paired]
    scores = [x[4] for x in paired]

    rho, _ = spearmanr(grvs, scores)

    import statistics
    sigma = statistics.stdev([x[0] for x in results])
    col_std = statistics.stdev([x[3] for x in results])

    return {
        "tau": tau, "w_d": w_d, "w_s": w_s, "w_c": w_c,
        "rho": rho,
        "v1_pass": sigma > 0.05,
        "v5_pass": rho < 0,
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

print(f"{'tau':>5}  {'w_d':>5}  {'w_s':>5}  {'w_c':>5}  {'rho':>7}  "
      f"{'V1':>4}  {'V5':>4}  {'V6c':>4}  {'sigma':>6}  {'col_std':>7}")

best = None
for tau in taus:
    for w_d, w_s, w_c in weight_sets:
        res = run_and_eval(tau, w_d, w_s, w_c)
        if res is None:
            continue
        all_pass = res["v1_pass"] and res["v5_pass"]
        marker = " ***" if all_pass else ""
        print(f"{tau:5.2f}  {w_d:5.2f}  {w_s:5.2f}  {w_c:5.2f}  {res['rho']:7.4f}  "
              f"{'Y' if res['v1_pass'] else 'N':>4}  {'Y' if res['v5_pass'] else 'N':>4}  "
              f"{'Y' if res['v6_col_pass'] else 'N':>4}  {res['sigma']:6.4f}  "
              f"{res['col_std']:7.4f}{marker}")
        if all_pass and (best is None or abs(res["rho"]) > abs(best["rho"])):
            best = res

print()
if best:
    print("=== BEST ===")
    print(f"tau={best['tau']}, w_d={best['w_d']}, w_s={best['w_s']}, w_c={best['w_c']}")
    print(f"rho={best['rho']:.4f}, sigma={best['sigma']:.4f}")
else:
    print("No combination passed V-1/V-5")
