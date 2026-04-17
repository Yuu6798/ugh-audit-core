"""analysis/run_incremental_calibration.py — 暫定 Phase E 校正

annotation_ui.py の batch 区切りで呼び出し、現時点までの
HA48 + accept40 を結合して calibrate_phase_e_thresholds.py を暫定実行する。

結果を短く表示し、fire_rate ∈ [10%, 30%] の閾値ペアが見つかれば STOP
推奨、そうでなければ CONTINUE を stdout に吐く。

使い方:
    python analysis/run_incremental_calibration.py
    python analysis/run_incremental_calibration.py --no-run-full  (dry-run)
"""
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from analysis.merge_ha48_accept40 import (  # noqa: E402
    load_acc40,
    load_ha48,
    filter_accept_subset,
)

ACC40_DEFAULT = (
    ROOT / "data" / "human_annotation_accept40" / "annotation_accept40.csv"
)
CAL_SCRIPT = ROOT / "analysis" / "calibrate_phase_e_thresholds.py"
GRID_CSV = ROOT / "analysis" / "phase_e_calibration_grid.csv"

# 受入基準 (docs/annotation_protocol.md §4)
ACCEPT_SUBSET_TARGET = 28
FIRE_RATE_MIN = 0.10
FIRE_RATE_MAX = 0.30


def current_accept_subset_size(acc40_path: Path) -> int:
    ha48 = load_ha48()
    acc40 = load_acc40(acc40_path)
    subset = filter_accept_subset(ha48 + acc40)
    return len(subset)


def parse_grid_for_fire_window(
    path: Path, fmin: float = FIRE_RATE_MIN, fmax: float = FIRE_RATE_MAX
) -> List[dict]:
    """fire_rate が目標ウィンドウに入る行を抽出."""
    if not path.exists():
        return []
    out: List[dict] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                fr = float(row.get("fire_rate", ""))
            except (TypeError, ValueError):
                continue
            if fmin <= fr <= fmax:
                out.append(row)
    return out


def run_calibrate_script() -> int:
    """calibrate_phase_e_thresholds.py を呼び出す。戻り値は exit code."""
    if not CAL_SCRIPT.exists():
        print(f"[ERROR] 校正スクリプトが見つからない: {CAL_SCRIPT}")
        return 2
    result = subprocess.run(
        [sys.executable, str(CAL_SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[ERROR] 校正スクリプト失敗")
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
    return result.returncode


def report(acc40_path: Path, ran_full: bool) -> str:
    """STOP / CONTINUE を判定して stdout テキストを返す."""
    n = current_accept_subset_size(acc40_path)
    lines: List[str] = []
    lines.append(f"accept subset 現在: n={n} (目標 {ACCEPT_SUBSET_TARGET})")

    grid_rows: List[dict] = []
    if ran_full:
        grid_rows = parse_grid_for_fire_window(GRID_CSV)

    if n < ACCEPT_SUBSET_TARGET:
        lines.append(f"  → CONTINUE (subset n={n} < {ACCEPT_SUBSET_TARGET})")
    elif not ran_full:
        lines.append(
            "  → 目標到達。--run-full で校正再実行を推奨"
        )
    elif grid_rows:
        # 最良候補 (ρ_advisory 降順で拾う)
        def rho_key(r: dict) -> float:
            try:
                return float(r.get("rho_advisory_full", "0") or 0)
            except ValueError:
                return 0.0
        grid_rows.sort(key=rho_key, reverse=True)
        best = grid_rows[0]
        tau_c = best.get("tau_collapse_high", "?")
        tau_a = best.get("tau_anchor_low", "?")
        fr = best.get("fire_rate", "?")
        rho = best.get("rho_advisory_full", "?")
        lines.append(
            f"  → STOP 推奨: τ_c={tau_c}, τ_a={tau_a}, fire_rate={fr}, "
            f"ρ_advisory_full={rho}"
        )
    else:
        lines.append(
            "  → 到達したが fire_rate ∈ "
            f"[{FIRE_RATE_MIN:.2f}, {FIRE_RATE_MAX:.2f}] 未発見。"
            "grid レンジの再確認が必要"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--acc40", type=Path, default=ACC40_DEFAULT)
    parser.add_argument(
        "--no-run-full", action="store_true",
        help="校正スクリプト本体を走らせずサブセット数だけ確認",
    )
    args = parser.parse_args(argv)

    n = current_accept_subset_size(args.acc40)
    if n < ACCEPT_SUBSET_TARGET:
        # 目標未達なら校正は意味がないのでスキップして CONTINUE を返す
        print(report(args.acc40, ran_full=False))
        return 0

    ran_full = not args.no_run_full
    if ran_full:
        rc = run_calibrate_script()
        if rc != 0:
            print(report(args.acc40, ran_full=False))
            return rc

    print(report(args.acc40, ran_full=ran_full))
    return 0


if __name__ == "__main__":
    sys.exit(main())
