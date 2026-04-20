"""tests/test_examples.py
examples/ 配下のサンプルが import + 実行で壊れていないことを検証する回帰テスト。
"""
from __future__ import annotations

import re
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

    case_verdicts: dict[int, str] = {}
    for line in result.stdout.splitlines():
        match = re.search(r"^\[(\d+)\].*verdict=([a-z_]+)", line)
        if match:
            case_verdicts[int(match.group(1))] = match.group(2)

    assert len(case_verdicts) == 3, (
        f"ケース行の verdict 抽出に失敗: {case_verdicts}\n{result.stdout}"
    )

    # q001 良質回答ケース（case 1） → accept
    assert case_verdicts[1] == "accept", (
        f"q001 良質ケースの verdict が想定外: {case_verdicts[1]}\n{result.stdout}"
    )

    # q001 ショートカットケース（case 2） → rewrite または regenerate
    assert case_verdicts[2] in {"rewrite", "regenerate"}, (
        f"q001 ショートカットケースの verdict が想定外: {case_verdicts[2]}\n"
        f"{result.stdout}"
    )

    # 3 ケース全体で verdict が 2 種類以上出ていること
    # （全件 accept や全件 degraded で誤って pass しないようにする）
    distinct = set(case_verdicts.values())
    assert len(distinct) >= 2, (
        f"verdict に多様性がない（パイプラインが定値を返している疑い）: {distinct}\n"
        f"{result.stdout}"
    )
