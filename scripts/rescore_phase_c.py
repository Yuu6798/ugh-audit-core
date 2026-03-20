#!/usr/bin/env python3
"""
rescore_phase_c.py — sentence-transformers 環境で Phase C v1 成果物を再生成する

生成物:
- data/phase_c_v1/phase_c_scored_v1.jsonl
- data/phase_c_v1/phase_c_v1_results.csv
- data/phase_c_v1/phase_c_report_v1.html

Usage:
    python3 scripts/rescore_phase_c.py
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_INPUT = REPO_ROOT / "data/phase_c_v0/phase_c_raw.jsonl"
V1_DIR = REPO_ROOT / "data/phase_c_v1"
SCORED_OUT = V1_DIR / "phase_c_scored_v1.jsonl"
CSV_OUT = V1_DIR / "phase_c_v1_results.csv"
HTML_OUT = V1_DIR / "phase_c_report_v1.html"


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)


def ensure_st_backend() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from ugh_audit import UGHScorer

    scorer = UGHScorer()
    print(f"backend detected: {scorer.backend}")
    if scorer.backend != "sentence-transformers":
        raise SystemExit(
            f"Expected sentence-transformers backend, got: {scorer.backend}"
        )


def backup_if_exists(path: Path) -> None:
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        print(f"backup: {bak}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase C v1 をSTバックエンドで再生成")
    parser.add_argument(
        "--reference-field",
        default="reference_core",
        choices=["reference_core", "reference"],
        help="ΔE計算に使うreferenceフィールド",
    )
    args = parser.parse_args()

    if not RAW_INPUT.exists():
        raise SystemExit(f"Missing input: {RAW_INPUT}")

    V1_DIR.mkdir(parents=True, exist_ok=True)
    ensure_st_backend()

    for p in [SCORED_OUT, CSV_OUT, HTML_OUT]:
        backup_if_exists(p)

    run([
        sys.executable,
        "scripts/score_phase_c.py",
        "--input", str(RAW_INPUT),
        "--output", str(SCORED_OUT),
        "--reference-field", args.reference_field,
    ])

    run([
        sys.executable,
        "scripts/export_phase_c.py",
        "--input", str(SCORED_OUT),
        "--version", "v1",
        "--outdir", str(V1_DIR),
    ])

    print("done")


if __name__ == "__main__":
    main()
