"""merge_annotations_48.py — HA20 + HA28 を統一スキーマで結合

出力スキーマ: id, category, S, C, O, propositions_hit, notes
- HA20: O = human_score, propositions_hit はそのまま, S/C は空欄（遡及アノテーション待ち）
- HA28: S/C/O はそのまま, propositions_hit は空欄（後で算出）
- category はベースライン CSV から取得
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- ベースラインから category を取得 ---
baseline_path = ROOT / "data" / "eval" / "audit_102_main_baseline_cascade.csv"
category_map: dict[str, str] = {}
with baseline_path.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        category_map[row["id"]] = row["category"]

# --- HA20 読み込み ---
ha20_path = ROOT / "data" / "human_annotation_20" / "human_annotation_20_completed.csv"
ha20_rows: list[dict[str, str]] = []
with ha20_path.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        qid = row["id"]
        ha20_rows.append({
            "id": qid,
            "category": category_map.get(qid, row.get("category", "")),
            "S": "",  # 遡及アノテーション待ち
            "C": "",  # 遡及アノテーション待ち
            "O": row["human_score"],
            "propositions_hit": row["propositions_hit"],
            "notes": row["notes"],
        })

# --- HA28 読み込み ---
ha28_path = ROOT / "data" / "human_annotation_28" / "annotation_28_results.csv"
ha28_rows: list[dict[str, str]] = []
with ha28_path.open(encoding="utf-8") as f:
    for row in csv.DictReader(f):
        qid = row["qid"]
        ha28_rows.append({
            "id": qid,
            "category": category_map.get(qid, ""),
            "S": row["S"],
            "C": row["C"],
            "O": row["O"],
            "propositions_hit": "",  # 後で算出
            "notes": row["note"],
        })

# --- 結合 & ソート ---
merged = sorted(ha20_rows + ha28_rows, key=lambda r: int(r["id"].replace("q", "")))

# --- 重複チェック ---
ids = [r["id"] for r in merged]
if len(ids) != len(set(ids)):
    dupes = [qid for qid in ids if ids.count(qid) > 1]
    raise ValueError(f"重複 ID 検出: {set(dupes)}")

# --- 出力 ---
out_dir = ROOT / "data" / "human_annotation_48"
out_dir.mkdir(exist_ok=True)
out_path = out_dir / "annotation_48_merged.csv"

fieldnames = ["id", "category", "S", "C", "O", "propositions_hit", "notes"]
with out_path.open("w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(merged)

# --- サマリー出力 ---
ha20_ids = {r["id"] for r in ha20_rows}
ha28_ids = {r["id"] for r in ha28_rows}
print(f"HA20: {len(ha20_rows)} 件")
print(f"HA28: {len(ha28_rows)} 件")
print(f"合計: {len(merged)} 件 (重複なし)")
print(f"HA20 S/C 未入力: {sum(1 for r in merged if r['id'] in ha20_ids and r['S'] == '')} 件")
print(f"HA28 propositions_hit 未入力: {sum(1 for r in merged if r['id'] in ha28_ids and r['propositions_hit'] == '')} 件")
print(f"出力: {out_path}")
