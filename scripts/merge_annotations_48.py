"""merge_annotations_48.py — HA20 + HA28 を統一スキーマで結合

出力スキーマ: id, category, S, C, O, propositions_hit, notes
- HA20: O = human_score, S/C は annotation_spec_v2 遡及テーブルから取得
- HA28: S/C/O はそのまま, propositions_hit は空欄（後で算出）
- category はベースライン CSV から取得
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --- annotation_spec_v2 遡及テーブル（HA20 の S/C 確定値） ---
HA20_RETRO_SC: dict[str, tuple[int, int]] = {
    "q032": (1, 1),
    "q024": (2, 1),
    "q095": (2, 1),
    "q015": (3, 1),
    "q025": (3, 1),
    "q037": (3, 1),
    "q033": (2, 2),
    "q044": (3, 2),
    "q069": (3, 1),
    "q012": (3, 2),
    "q019": (3, 2),
    "q061": (3, 2),
    "q071": (3, 2),
    "q100": (2, 2),
    "q049": (3, 3),
    "q075": (3, 3),
    "q080": (3, 3),
    "q009": (3, 3),
    "q083": (3, 3),
    "q063": (3, 3),
}

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
        retro = HA20_RETRO_SC.get(qid)
        if retro is None:
            raise ValueError(f"遡及テーブルに {qid} が存在しない")
        ha20_rows.append({
            "id": qid,
            "category": category_map.get(qid, row.get("category", "")),
            "S": str(retro[0]),
            "C": str(retro[1]),
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
print(f"HA20: {len(ha20_rows)} 件 (S/C: 遡及テーブルから全件埋め済み)")
print(f"HA28: {len(ha28_rows)} 件")
print(f"合計: {len(merged)} 件 (重複なし)")
s_filled = sum(1 for r in merged if r["S"] != "")
c_filled = sum(1 for r in merged if r["C"] != "")
print(f"S 入力済み: {s_filled}/{len(merged)}, C 入力済み: {c_filled}/{len(merged)}")
print(f"HA28 propositions_hit 未入力: {sum(1 for r in merged if r['id'] in ha28_ids and r['propositions_hit'] == '')} 件")
print(f"出力: {out_path}")
