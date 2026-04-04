"""batch_audit_102.py — 102問全件検証スクリプト

Usage:
    python batch_audit_102.py \
        --questions data/question_sets/ugh-audit-100q-v3-1.jsonl.txt \
        --responses data/phase_c_scored_v1_t0_only.jsonl \
        --structural-gate data/structural_gate_summary.csv \
        --out audit_102_results.csv

出力:
    1. audit_102_results.csv — 全件の検出結果・判定結果・SG比較
    2. stdout — サマリー統計

注意:
    --questions と --responses のパスはリポジトリ構成に合わせて調整すること。
    --responses は phase_c_scored_v1_t0_only.jsonl（t=0.0のresponse入り）。
    --structural-gate は任意（なければSG比較をスキップ）。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

# リポジトリルートからインポート
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ugh_calculator import calculate
from detector import detect
from decider import decide


def load_responses(path: str) -> dict[str, dict]:
    """phase_c_scored_v1_t0_only.jsonl を読む"""
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            data[obj["id"]] = obj
    return data


def load_questions(path: str) -> dict[str, dict]:
    """ugh-audit-100q-v3-1 を読む（questions側にresponseがない場合の別ソース）"""
    data = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            data[obj["id"]] = obj
    return data


def load_structural_gate(path: str) -> dict[str, dict]:
    """structural_gate_summary.csv (t=0.0のみ) を読む"""
    data = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("temperature") == "0.0":
                data[row["id"]] = row
    return data


def run_audit(qid: str, response_text: str, question_meta: dict) -> dict:
    """1件の監査を実行"""
    evidence = detect(qid, response_text, question_meta)
    state = calculate(evidence)
    result = decide(state, evidence)
    return {
        "evidence": evidence,
        "state": state,
        "policy": result["policy"],
        "budget": result["budget"],
    }


def main():
    parser = argparse.ArgumentParser(description="102問全件検証")
    parser.add_argument("--questions", required=True, help="メタデータJSONLパス")
    parser.add_argument("--responses", required=True, help="response入りJSONLパス")
    parser.add_argument("--structural-gate", default=None, help="SG CSVパス（任意）")
    parser.add_argument("--out", default="audit_102_results.csv", help="出力CSVパス")
    args = parser.parse_args()

    # データ読み込み
    questions = load_questions(args.questions)
    responses = load_responses(args.responses)
    sg = load_structural_gate(args.structural_gate) if args.structural_gate else {}

    print(f"Questions: {len(questions)}, Responses: {len(responses)}, SG: {len(sg)}")

    # 全件実行
    rows = []
    errors = []

    for qid in sorted(responses.keys()):
        resp_data = responses[qid]
        response_text = resp_data.get("response", "")

        # メタデータは questions 側から取る（core_propositions等が必要）
        # responses側にもquestion等があるので、マージする
        q_meta = questions.get(qid, {})
        if not q_meta:
            # questionsに無ければresponses側のデータを使う
            q_meta = resp_data
        else:
            # responses側のtrap_type等をq_metaに補完（questions側に無い場合）
            for key in ("trap_type", "question", "category"):
                if not q_meta.get(key) and resp_data.get(key):
                    q_meta[key] = resp_data[key]
            # original_* フィールドを標準名にフォールバック
            if not q_meta.get("trap_type") and q_meta.get("original_trap_type"):
                q_meta["trap_type"] = q_meta["original_trap_type"]

        try:
            result = run_audit(qid, response_text, q_meta)
            ev = result["evidence"]
            st = result["state"]
            pol = result["policy"]

            sg_row = sg.get(qid, {})

            rows.append({
                "id": qid,
                "category": q_meta.get("category", ""),
                "trap_type": q_meta.get("trap_type", ""),
                # detector output
                "f1": ev.f1_anchor,
                "f2": ev.f2_unknown,
                "f3": ev.f3_operator,
                "f4": ev.f4_premise,
                "f2_detail": ev.f2_detail,
                "f3_family": ev.f3_operator_family,
                "f4_detail": ev.f4_detail,
                "hits": ev.propositions_hit,
                "total": ev.propositions_total,
                "hit_ids": str(ev.hit_ids),
                "miss_ids": str(ev.miss_ids),
                # calculator output
                "S": st.S,
                "C": st.C,
                "dE": st.delta_e,
                "dE_bin": st.delta_e_bin,
                "C_bin": st.C_bin,
                # decision
                "decision": pol["decision"],
                "repair_order": str(pol["repair_order"]),
                "budget_cost": result["budget"]["total_cost"],
                # SG comparison (empty if no SG data)
                "sg_f1": sg_row.get("f1_flag", ""),
                "sg_f2": sg_row.get("f2_flag", ""),
                "sg_f3": sg_row.get("f3_flag", ""),
                "sg_f4": sg_row.get("f4_flag", ""),
                "sg_verdict": sg_row.get("verdict", ""),
                "sg_primary": sg_row.get("primary_element", ""),
            })
        except Exception as e:
            errors.append(f"{qid}: {e}")

    if errors:
        print(f"\n=== ERRORS ({len(errors)}) ===")
        for err in errors:
            print(f"  {err}")

    # CSV出力
    if rows:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nCSV written: {args.out} ({len(rows)} rows)")

    # === サマリー統計 ===
    print(f"\n{'='*60}")
    print("102問全件検証サマリー")
    print(f"{'='*60}")

    # Decision分布
    decisions = Counter(r["decision"] for r in rows)
    print("\n--- Decision分布 ---")
    for d in ["accept", "rewrite", "regenerate"]:
        print(f"  {d}: {decisions.get(d, 0)}")

    # f1-f4 発火率
    print("\n--- f1-f4 発火率 ---")
    for f_name in ["f1", "f2", "f3", "f4"]:
        fired = sum(1 for r in rows if r[f_name] > 0)
        full = sum(1 for r in rows if r[f_name] >= 1.0)
        half = sum(1 for r in rows if r[f_name] == 0.5)
        print(f"  {f_name}: {fired}/102 fired (1.0: {full}, 0.5: {half})")

    # 命題検出統計
    print("\n--- 命題検出統計 ---")
    total_hits = sum(r["hits"] for r in rows)
    total_props = sum(r["total"] for r in rows)
    hit_rate = total_hits / total_props if total_props > 0 else 0
    print(f"  Total props: {total_props}")
    print(f"  Total hits: {total_hits}")
    print(f"  Hit rate: {hit_rate:.3f}")

    c3 = sum(1 for r in rows if r["C_bin"] == 3)
    c2 = sum(1 for r in rows if r["C_bin"] == 2)
    c1 = sum(1 for r in rows if r["C_bin"] == 1)
    print(f"  C_bin distribution: C=3: {c3}, C=2: {c2}, C=1: {c1}")

    # SG比較（あれば）
    if sg:
        print("\n--- Structural Gate比較 ---")

        for f_name, sg_name in [("f1", "sg_f1"), ("f2", "sg_f2"), ("f3", "sg_f3"), ("f4", "sg_f4")]:
            match = mismatch_pos = mismatch_neg = 0
            total = 0
            mismatches = []
            for r in rows:
                if r[sg_name] == "":
                    continue
                total += 1
                sg_val = float(r[sg_name])
                det_val = r[f_name]
                if (sg_val > 0 and det_val > 0) or (sg_val == 0 and det_val == 0):
                    match += 1
                elif sg_val > 0 and det_val == 0:
                    mismatch_neg += 1  # SG detected, we missed
                    mismatches.append(f"{r['id']}(SG={sg_val},det={det_val})")
                else:
                    mismatch_pos += 1  # we detected, SG didn't
                    mismatches.append(f"{r['id']}(SG={sg_val},det={det_val})")

            print(f"  {f_name}: {match}/{total} match | false_pos={mismatch_pos} false_neg={mismatch_neg}")
            if mismatches and len(mismatches) <= 10:
                print(f"    mismatches: {', '.join(mismatches)}")
            elif mismatches:
                print(f"    mismatches (first 10): {', '.join(mismatches[:10])}")

        # Verdict alignment
        sg_align = 0
        sg_total = 0
        sg_mismatches = []
        for r in rows:
            v = r["sg_verdict"]
            d = r["decision"]
            if not v:
                continue
            sg_total += 1
            if (v == "fail" and d in ("rewrite", "regenerate")) or (v == "pass" and d == "accept"):
                sg_align += 1
            else:
                sg_mismatches.append(f"{r['id']}(sg={v},dec={d})")

        print(f"\n  Verdict alignment: {sg_align}/{sg_total} ({sg_align/sg_total*100:.1f}%)")
        if sg_mismatches:
            print(f"  Mismatches ({len(sg_mismatches)}):")
            for m in sg_mismatches[:20]:
                print(f"    {m}")

    # trap_type別のdecision分布
    print("\n--- trap_type別 decision ---")
    trap_decisions: dict[str, Counter] = {}
    for r in rows:
        tt = r["trap_type"]
        if tt not in trap_decisions:
            trap_decisions[tt] = Counter()
        trap_decisions[tt][r["decision"]] += 1

    for tt in sorted(trap_decisions.keys()):
        cd = trap_decisions[tt]
        total = sum(cd.values())
        parts = ", ".join(f"{k}:{v}" for k, v in sorted(cd.items()))
        print(f"  {tt} ({total}): {parts}")


if __name__ == "__main__":
    main()
