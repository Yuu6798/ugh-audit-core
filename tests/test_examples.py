"""tests/test_examples.py
examples/ 配下のサンプルが import + 実行で壊れていないことを検証する回帰テスト。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = REPO_ROOT / "examples" / "basic_audit.py"


def test_basic_audit_runs_to_completion():
    """python examples/basic_audit.py が exit 0 で完了し、
    かつ 3 ケースで verdict の多様性が観測されることを検証する。

    detect → calculate → decide パイプライン全体が壊れていないことの
    最低限の smoke test。具体値（ΔE 等）は非アサート — 数値変更は
    校正で起きうるため、verdict 分類の差分のみを固定する。
    """
    result = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, (
        f"example exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )

    # q001 良質回答ケース → accept が出ていること
    assert "verdict=accept" in result.stdout, (
        f"q001 良質ケースで verdict=accept が出ていない:\n{result.stdout}"
    )

    # q001 ショートカットケース → rewrite または regenerate が出ていること
    assert (
        "verdict=rewrite" in result.stdout
        or "verdict=regenerate" in result.stdout
    ), (
        f"q001 ショートカットケースで rewrite/regenerate が出ていない:\n{result.stdout}"
    )

    # 3 ケース全体で verdict が 2 種類以上出ていること
    # （全件 accept や全件 degraded で誤って pass しないようにする）
    distinct = {
        v
        for v in ("accept", "rewrite", "regenerate", "degraded")
        if f"verdict={v}" in result.stdout
    }
    assert len(distinct) >= 2, (
        f"verdict に多様性がない（パイプラインが定値を返している疑い）: {distinct}\n"
        f"{result.stdout}"
    )
