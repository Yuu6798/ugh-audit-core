#!/usr/bin/env python3
"""Phase 0: 実験データの準備 — phase_c + question_set + human_annotation を結合."""
from __future__ import annotations

import csv
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def main() -> None:
    base = Path(__file__).resolve().parent
    repo_root = base.parent.parent

    # --- 1. phase_c t=0.0 データ (102件) ---
    phase_c = load_jsonl(repo_root / "data" / "phase_c_scored_v1_t0_only.jsonl")
    print(f"Phase C t=0.0: {len(phase_c)} records")

    # --- 2. 100q question set (core_propositions を含む) ---
    qs_path = repo_root / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
    question_set = {r["id"]: r for r in load_jsonl(qs_path)}
    print(f"Question set: {len(question_set)} questions")

    # --- 3. human annotation 20件 ---
    ha_path = repo_root / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
    human_annot: dict[str, dict] = {}
    with open(ha_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            human_annot[row["id"]] = row
    print(f"Human annotations: {len(human_annot)} records")

    # --- 結合 ---
    experiment_data = []
    for rec in phase_c:
        qid = rec["id"]
        qs = question_set.get(qid, {})
        ha = human_annot.get(qid)

        core_props = qs.get("core_propositions", [])
        # phase_c にも meta_original_core_propositions がある場合はそちらを優先
        if "meta_original_core_propositions" in rec:
            raw = rec["meta_original_core_propositions"]
            if isinstance(raw, str):
                try:
                    core_props = json.loads(raw)
                except json.JSONDecodeError:
                    pass
            elif isinstance(raw, list):
                core_props = raw

        entry = {
            "id": qid,
            "category": rec.get("category", qs.get("category", "")),
            "question": rec.get("question", ""),
            "response": rec.get("response", ""),
            "reference": rec.get("reference", ""),
            "reference_core": rec.get("reference_core", qs.get("reference_core", "")),
            "core_propositions": core_props,
            "existing_por": float(rec.get("por", 0)),
            "existing_delta_e": float(rec.get("delta_e_full", rec.get("delta_e", 0))),
            "has_human_annotation": ha is not None,
        }

        if ha:
            entry["human_score"] = float(ha["human_score"])
            entry["existing_hit"] = ha.get("propositions_hit", "")
            entry["notes"] = ha.get("notes", "")
        else:
            entry["human_score"] = None
            entry["existing_hit"] = None
            entry["notes"] = None

        experiment_data.append(entry)

    # ソート: human annotation ありを先頭に
    experiment_data.sort(key=lambda x: (not x["has_human_annotation"], x["id"]))

    out_path = base / "experiment_input.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(experiment_data, f, ensure_ascii=False, indent=2)

    n_annotated = sum(1 for d in experiment_data if d["has_human_annotation"])
    n_with_props = sum(1 for d in experiment_data if d["core_propositions"])
    print(f"\nOutput: {out_path}")
    print(f"  Total: {len(experiment_data)}")
    print(f"  With human annotation: {n_annotated}")
    print(f"  With core_propositions: {n_with_props}")


if __name__ == "__main__":
    main()
