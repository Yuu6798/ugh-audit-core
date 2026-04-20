"""analysis/mode_grv_ha48_check.py — mode_conditioned_grv v2 HA48 検証

HA48 の grv 結果 + mode_affordance から 4 成分ベクトルを計算し、
モード別の分布とヒューマンスコアとの相関を確認する。

使い方:
    python analysis/mode_grv_ha48_check.py
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scipy.stats import spearmanr  # type: ignore[import-untyped]  # noqa: E402

from grv_calculator import compute_grv  # noqa: E402
from mode_grv import compute_mode_conditioned_grv  # noqa: E402

# --- データ読み込み ---
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
Q_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.jsonl"
CANONICAL_PATH = ROOT / "data" / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
RESPONSES_PATH = ROOT / "data" / "phase_c_scored_v1_t0_only.jsonl"


def main() -> None:
    # Load HA48
    ha48 = {}
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ha48[row["id"]] = row

    # Load q_meta (for grv computation)
    q_meta = {}
    with open(Q_META_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["id"] in ha48:
                q_meta[rec["id"]] = rec

    # Load canonical (for mode_affordance)
    canonical = {}
    with open(CANONICAL_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec["id"] in ha48 and "mode_affordance" in rec:
                canonical[rec["id"]] = rec["mode_affordance"]

    # Load responses
    responses = {}
    with open(RESPONSES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            qid = rec.get("question_id") or rec.get("id")
            if qid in ha48:
                responses[qid] = rec

    ids = sorted(ha48.keys() & q_meta.keys() & canonical.keys() & responses.keys())
    print(f"データ: HA48={len(ha48)}, canonical mode={len(canonical)}, overlap={len(ids)}")

    # Mode distribution
    mode_counts = Counter(canonical[qid]["primary"] for qid in ids)
    print(f"\nモード分布: {dict(mode_counts)}")

    # Compute grv + mode_conditioned_grv
    results = []
    for qid in ids:
        meta = q_meta[qid]
        resp = responses[qid]
        ma = canonical[qid]

        grv_r = compute_grv(
            question=meta["question"],
            response_text=resp.get("response", ""),
            question_meta=meta,
            metadata_source="inline",
        )
        if grv_r is None:
            continue

        mcg = compute_mode_conditioned_grv(
            grv_result=grv_r,
            response_text=resp.get("response", ""),
            mode_affordance_primary=ma["primary"],
            action_required=ma.get("action_required", False),
        )
        if mcg is None:
            continue

        o = float(ha48[qid]["O"])
        results.append({
            "id": qid, "O": o, "mode": ma["primary"],
            "anchor": mcg.anchor_alignment,
            "balance": mcg.balance,
            "boilerplate": mcg.boilerplate_risk,
            "collapse": mcg.collapse_risk,
            "grv": grv_r.grv,
            "focus": mcg.focus_components,
        })

    print(f"\n計算成功: {len(results)}/{len(ids)}")

    # --- 全体相関 ---
    print("\n--- 全体 Spearman ρ (vs O) ---")
    for comp in ["anchor", "boilerplate", "collapse", "grv"]:
        paired = [(r[comp], r["O"]) for r in results if r[comp] is not None]
        if len(paired) < 5:
            print(f"  {comp}: n={len(paired)} (データ不足)")
            continue
        rho, p = spearmanr([v[0] for v in paired], [v[1] for v in paired])
        print(f"  {comp}: ρ={rho:.4f} (p={p:.4f}, n={len(paired)})")

    # balance は comparative/exploratory のみ
    bal = [(r["balance"], r["O"]) for r in results
           if r["balance"] is not None and r["mode"] in ("comparative", "exploratory")]
    if len(bal) >= 3:
        rho, p = spearmanr([v[0] for v in bal], [v[1] for v in bal])
        print(f"  balance (comp/expl): ρ={rho:.4f} (n={len(bal)})")
    else:
        print(f"  balance (comp/expl): n={len(bal)} (データ不足)")

    # --- モード別統計 ---
    print("\n--- モード別 mean(component) ---")
    by_mode = defaultdict(list)
    for r in results:
        by_mode[r["mode"]].append(r)

    print(f"{'mode':<14} {'n':>3} {'mean_O':>7} {'anchor':>7} "
          f"{'boiler':>7} {'collapse':>8} {'balance':>8}")
    for mode in sorted(by_mode.keys()):
        items = by_mode[mode]
        n = len(items)
        mo = statistics.mean([r["O"] for r in items])
        ma = statistics.mean([r["anchor"] for r in items])
        mb = statistics.mean([r["boilerplate"] for r in items])
        col_items = [r["collapse"] for r in items if r["collapse"] is not None]
        mc = statistics.mean(col_items) if col_items else float("nan")
        bal_items = [r["balance"] for r in items if r["balance"] is not None]
        mbal = statistics.mean(bal_items) if bal_items else float("nan")
        print(f"{mode:<14} {n:>3} {mo:>7.2f} {ma:>7.4f} "
              f"{mb:>7.4f} {mc:>8.4f} {mbal:>8.4f}")

    # --- Per-question (focus 成分のみ highlight) ---
    print("\n--- Per-question (sorted by O) ---")
    print(f"{'id':>6} {'O':>3} {'mode':<14} {'anchor':>7} "
          f"{'boiler':>7} {'collapse':>8} {'balance':>8} focus")
    for r in sorted(results, key=lambda x: x["O"]):
        bal_str = f"{r['balance']:>8.4f}" if r["balance"] is not None else "     N/A"
        col_str = f"{r['collapse']:>8.4f}" if r["collapse"] is not None else "     N/A"
        print(f"{r['id']:>6} {r['O']:>3.0f} {r['mode']:<14} {r['anchor']:>7.4f} "
              f"{r['boilerplate']:>7.4f} {col_str} {bal_str} {r['focus']}")


if __name__ == "__main__":
    main()
