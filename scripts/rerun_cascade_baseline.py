"""rerun_cascade_baseline.py — cascade 統合後の 102問全件リラン

batch_audit_102.py と同じパイプラインを実行し、
cascade 統合後の hit_source 内訳を含む新ベースラインCSVを出力する。
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# リポジトリルートからインポート
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ugh_calculator import calculate  # noqa: E402
from detector import detect  # noqa: E402
from decider import decide  # noqa: E402


def load_jsonl(path: str) -> dict[str, dict]:
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
    data = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("temperature") == "0.0":
                data[row["id"]] = row
    return data


def load_atomic_units_map(csv_path: str) -> dict[str, dict[int, list[str]]]:
    """dev_cascade_20.csv から question_id → {prop_idx: [atomic_units]} のマップを構築"""
    result: dict[str, dict[int, list[str]]] = defaultdict(dict)
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qid = row["question_id"]
            pidx = int(row["prop_idx"])
            raw = row.get("atomic_units", "[]")
            try:
                units = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                units = []
            result[qid][pidx] = units
    return dict(result)


def main():
    questions_path = ROOT / "data/question_sets/ugh-audit-100q-v3-1.jsonl"
    responses_path = ROOT / "data/phase_c_scored_v1_t0_only.jsonl"
    sg_path = ROOT / "data/gate_results/structural_gate_summary.csv"
    atomic_path = ROOT / "data/eval/dev_cascade_20.csv"
    out_path = ROOT / "data/eval/audit_102_main_baseline_cascade.csv"

    questions = load_jsonl(str(questions_path))
    responses = load_jsonl(str(responses_path))
    sg = load_structural_gate(str(sg_path)) if sg_path.exists() else {}
    atomic_map = load_atomic_units_map(str(atomic_path)) if atomic_path.exists() else {}

    print(f"Questions: {len(questions)}, Responses: {len(responses)}, SG: {len(sg)}")
    print(f"Atomic units available for {len(atomic_map)} questions")

    rows = []
    errors = []

    for qid in sorted(responses.keys()):
        resp_data = responses[qid]
        response_text = resp_data.get("response", "")

        q_meta = questions.get(qid, {})
        if not q_meta:
            q_meta = resp_data
        else:
            for key in ("trap_type", "question", "category"):
                if not q_meta.get(key) and resp_data.get(key):
                    q_meta[key] = resp_data[key]
            if not q_meta.get("trap_type") and q_meta.get("original_trap_type"):
                q_meta["trap_type"] = q_meta["original_trap_type"]

        # atomic_units_map を question_meta に注入
        if qid in atomic_map:
            q_meta["atomic_units_map"] = atomic_map[qid]

        try:
            evidence = detect(qid, response_text, q_meta)
            state = calculate(evidence)
            result = decide(state, evidence)
            pol = result["policy"]
            sg_row = sg.get(qid, {})

            rows.append({
                "id": qid,
                "category": q_meta.get("category", ""),
                "trap_type": q_meta.get("trap_type", ""),
                "f1": evidence.f1_anchor,
                "f2": evidence.f2_unknown,
                "f3": evidence.f3_operator,
                "f4": evidence.f4_premise,
                "f2_detail": evidence.f2_detail,
                "f3_family": evidence.f3_operator_family,
                "f4_detail": evidence.f4_detail,
                "hits": evidence.propositions_hit,
                "total": evidence.propositions_total,
                "hit_ids": str(evidence.hit_ids),
                "miss_ids": str(evidence.miss_ids),
                "hit_sources": json.dumps(evidence.hit_sources, ensure_ascii=False),
                "S": state.S,
                "C": state.C,
                "dE": state.delta_e,
                "dE_bin": state.delta_e_bin,
                "C_bin": state.C_bin,
                "decision": pol["decision"],
                "repair_order": str(pol["repair_order"]),
                "budget_cost": result["budget"]["total_cost"],
                "sg_f1": sg_row.get("f1_flag", ""),
                "sg_f2": sg_row.get("f2_flag", ""),
                "sg_f3": sg_row.get("f3_flag", ""),
                "sg_f4": sg_row.get("f4_flag", ""),
                "sg_verdict": sg_row.get("verdict", ""),
                "sg_primary": sg_row.get("primary_element", ""),
            })
        except Exception as e:
            errors.append(f"{qid}: {e}")
            import traceback
            traceback.print_exc()

    if errors:
        print(f"\n=== ERRORS ({len(errors)}) ===")
        for err in errors:
            print(f"  {err}")

    # CSV 出力
    if rows:
        with open(out_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nCSV written: {out_path} ({len(rows)} rows)")

    # === 集計 ===
    print(f"\n{'='*60}")
    print("cascade 統合 102問全件リラン サマリー")
    print(f"{'='*60}")

    # hit_source 集計
    source_counts = Counter()
    all_rescued = []
    for r in rows:
        hs = json.loads(r["hit_sources"])
        for idx_str, src in hs.items():
            source_counts[src] += 1
            if src == "cascade_rescued":
                all_rescued.append((r["id"], int(idx_str)))

    total_props = sum(r["total"] for r in rows)
    total_hits = sum(r["hits"] for r in rows)

    print("\n--- hit_source 内訳 ---")
    print(f"  tfidf:            {source_counts['tfidf']}")
    print(f"  cascade_rescued:  {source_counts['cascade_rescued']}")
    print(f"  miss:             {source_counts['miss']}")
    print(f"  合計:             {sum(source_counts.values())} / {total_props}")
    print(f"\n  命題ヒット率: {total_hits}/{total_props} ({total_hits/total_props*100:.1f}%)")

    # cascade_rescued 詳細
    if all_rescued:
        print("\n--- cascade_rescued 命題一覧 ---")
        for qid, pidx in sorted(all_rescued):
            q_meta = questions.get(qid, {})
            props = q_meta.get("core_propositions", [])
            prop_text = props[pidx] if pidx < len(props) else "?"
            print(f"  {qid}_p{pidx}: {prop_text}")

    # Decision 分布
    decisions = Counter(r["decision"] for r in rows)
    print("\n--- Decision 分布 ---")
    for d in ["accept", "rewrite", "regenerate"]:
        print(f"  {d}: {decisions.get(d, 0)}")


if __name__ == "__main__":
    main()
