"""analysis/merge_ha48_accept40.py — HA48 + accept40 結合ユーティリティ

Phase E 再校正時に HA48 と新規アノテート分を union する。新規分は
annotation_ui.py が埋めた annotation_accept40.csv を読み込む。

結合後の CSV は `calibrate_phase_e_thresholds.py` がそのまま読めるよう
HA48 と同一サブセットのカラム `(id, O)` を保証する。

使い方:
    python analysis/merge_ha48_accept40.py
    python analysis/merge_ha48_accept40.py --acc40 path/to/annotation_accept40.csv
    python analysis/merge_ha48_accept40.py --accept-only
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent
HA48_PATH = ROOT / "data" / "human_annotation_48" / "annotation_48_merged.csv"
ACC40_DEFAULT = (
    ROOT / "data" / "human_annotation_accept40" / "annotation_accept40.csv"
)
OUT_DIR = ROOT / "data" / "human_annotation_accept40"
V5_PATH = ROOT / "data" / "eval" / "audit_102_main_baseline_v5.csv"

# HA48 / accept40 の O スケール (1-5 Likert)
O_SCALE_MIN = 1
O_SCALE_MAX = 5

DELTA_E_ACCEPT = 0.10


def _compute_delta_e(s: float, c: float) -> float:
    raw = 2.0 * (1.0 - s) + 1.0 * (1.0 - c)
    return max(0.0, min(1.0, raw / 3.0))


def _parse_o(raw: str) -> Optional[int]:
    """HA48 / accept40 の O 値を int 1-5 にパース.

    既存 HA48 には "1.0" "1" の両方が混在。空欄は未アノテート扱い。
    範囲外は None を返す。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        v = int(round(float(s)))
    except ValueError:
        return None
    if v < O_SCALE_MIN or v > O_SCALE_MAX:
        return None
    return v


def load_ha48() -> List[dict]:
    rows: List[dict] = []
    with open(HA48_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            o = _parse_o(row.get("O", ""))
            if o is None:
                continue
            rows.append({
                "id": row["id"],
                "O": o,
                "source": "ha48",
                "question_id": row["id"],
            })
    return rows


def load_acc40(path: Path) -> List[dict]:
    if not path.exists():
        return []
    rows: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("blind_check"):
                # ブラインド混入分は信頼性チェック用、校正データには含めない
                continue
            o = _parse_o(row.get("O", ""))
            if o is None:
                continue
            de_raw = (row.get("delta_e") or "").strip()
            try:
                de_val = float(de_raw) if de_raw else None
            except ValueError:
                de_val = None
            rows.append({
                "id": row["id"],
                "O": o,
                "source": row.get("source", "acc40"),
                "question_id": row.get("question_id", ""),
                "delta_e": de_val,
            })
    return rows


def _v5_accept_ids() -> Dict[str, float]:
    """v5 baseline で ΔE ≤ 0.10 を返す id → ΔE マップ."""
    result: Dict[str, float] = {}
    with open(V5_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                s = float(row["S"])
                c = float(row["C"])
            except (ValueError, KeyError):
                continue
            de = _compute_delta_e(s, c)
            if de <= DELTA_E_ACCEPT:
                result[row["id"]] = de
    return result


def filter_accept_subset(rows: Iterable[dict]) -> List[dict]:
    """ΔE ≤ 0.10 の問いに対応する行だけ残す.

    HA48 行は `id` が v5 の accept set にある場合、accept subset とみなす。
    accept40 行は `question_id` (元の質問) で照合。
    accept40 の source が v5_unannotated の場合は accept 相当として扱う。
    """
    accept_v5 = _v5_accept_ids()
    out: List[dict] = []
    for r in rows:
        src = r.get("source", "")
        if src == "ha48":
            if r["id"] in accept_v5:
                out.append(r)
        elif src == "v5_unannotated":
            # sampler で ΔE ≤ 0.10 のみを v5_unannotated にラベル付けしている
            out.append(r)
        elif src.startswith("orchestrator"):
            # orchestrator は新規 response なので v5 の accept 集合で判定しない。
            # 行自身の delta_e が記録されていればそれで accept 判定する。
            # delta_e 欠損 fallback のみ v5 の question_id で照合 (後方互換)。
            de = r.get("delta_e")
            if isinstance(de, (int, float)):
                if de <= DELTA_E_ACCEPT:
                    out.append(r)
            else:
                qid = r.get("question_id", "")
                if qid in accept_v5:
                    out.append(r)
        # v5_borderline は accept subset に含めない
    return out


def merge(acc40_path: Path, accept_only: bool) -> List[dict]:
    combined = load_ha48() + load_acc40(acc40_path)
    if accept_only:
        combined = filter_accept_subset(combined)
    return combined


def write_merged(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "O", "source", "question_id", "delta_e"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acc40", type=Path, default=ACC40_DEFAULT)
    parser.add_argument("--accept-only", action="store_true")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="出力 CSV (未指定時は accept-only に応じた default path)",
    )
    args = parser.parse_args(argv)

    rows = merge(args.acc40, args.accept_only)
    if args.out is None:
        name = (
            "annotation_merged_accept_subset.csv"
            if args.accept_only
            else "annotation_ha48_plus_accept40.csv"
        )
        out = OUT_DIR / name
    else:
        out = args.out

    write_merged(rows, out)
    print(f"結合: {len(rows)} 件")
    if args.accept_only:
        print("  accept subset フィルタ適用済み")
    print(f"出力: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
