"""tests/test_validation_ci.py — docs/validation.md §「信頼区間」の再現性テスト

Codex review P2 (PR #104 round 2) への恒久対応: **docs の table を直接 parse**
して Fisher z formula と突合する。hardcoded 定数だけで検証していた前版は、
docs 側のタイポや stale edit を検出できない false-sense-of-protection を
生んでいた。

この test で保証されること:
  1. docs/validation.md §「信頼区間」の table 行すべての CI 値が、記載
     された (ρ, n) から Fisher z formula で再現可能
  2. table 構造が想定通り (header, 5 行以上) — collapsing / deletion 検知
  3. docs に typo が入れば **ここで赤になる**
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

VALIDATION_MD = Path(__file__).resolve().parent.parent / "docs" / "validation.md"

# table 行の正規表現:
#   | label | n | ρ | [lo, hi] |
# ρ と境界値は +/- 符号どちらも許容、小数部は 1-5 桁想定
_CI_ROW_RE = re.compile(
    r"^\|\s*([^|]+?)\s*\|\s*"        # 1: label
    r"(\d+)\s*\|\s*"                  # 2: n
    r"([+-]?\d*\.?\d+)\s*\|\s*"       # 3: rho
    r"\[\s*([+-]?\d*\.?\d+)\s*,\s*"   # 4: lo
    r"([+-]?\d*\.?\d+)\s*\]\s*\|"     # 5: hi
    r"\s*$"
)

# Section heading that begins the CI table
_CI_SECTION_HEADING = "信頼区間 (95% CI"


def fisher_ci(rho: float, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Fisher z 変換ベースの Spearman ρ 信頼区間。

    docs/validation.md §「信頼区間」と同一計算式。
    """
    z = math.atanh(rho)
    se = 1.0 / math.sqrt(n - 3)
    zc = 1.959963984540054  # norm.ppf(1 - alpha/2) for alpha=0.05
    lo = math.tanh(z - zc * se)
    hi = math.tanh(z + zc * se)
    return lo, hi


def _parse_docs_ci_table() -> list[tuple[str, float, int, float, float]]:
    """docs/validation.md §「信頼区間」table を parse する。

    Returns: [(label, rho, n, lo, hi), ...]

    table が見つからない / 行数ゼロなら RuntimeError (silent no-op 防止)。
    """
    if not VALIDATION_MD.exists():
        raise RuntimeError(f"{VALIDATION_MD} not found")

    text = VALIDATION_MD.read_text(encoding="utf-8")
    rows: list[tuple[str, float, int, float, float]] = []
    in_ci_section = False

    for raw in text.splitlines():
        line = raw.rstrip()
        # section 境界検知: 次の ## heading に当たったら離脱
        if line.startswith("## "):
            if _CI_SECTION_HEADING in line:
                in_ci_section = True
                continue
            elif in_ci_section:
                # CI section を抜けた
                break
            else:
                continue
        if not in_ci_section:
            continue

        m = _CI_ROW_RE.match(line)
        if m:
            label = m.group(1).strip()
            n = int(m.group(2))
            rho = float(m.group(3))
            lo = float(m.group(4))
            hi = float(m.group(5))
            rows.append((label, rho, n, lo, hi))

    if not rows:
        raise RuntimeError(
            f"No CI table rows parsed from {VALIDATION_MD} §「{_CI_SECTION_HEADING}」. "
            "Section heading or table structure may have changed."
        )
    return rows


# --- sentinel: table 構造が想定通りであることを保証 ---


def test_docs_ci_table_is_parseable_and_complete() -> None:
    """docs/validation.md の CI table が parse 可能で 5 行以上あることを保証する。

    future doc refactor で table が削除・壊滅されても、ここで赤になるため
    parametrize が silent に no-op 化することを防ぐ二重防御。
    """
    rows = _parse_docs_ci_table()
    assert len(rows) >= 5, (
        f"CI table has only {len(rows)} rows (expected >= 5: "
        f"HA48 system/human/L_sem + HA20 system/human)"
    )
    # HA48 / HA20 ラベルが含まれていること（section 取り違え検知）
    labels = " ".join(r[0] for r in rows)
    assert "HA48" in labels, "HA48 row missing from CI table"
    assert "HA20" in labels, "HA20 row missing from CI table"


# --- 本体: docs の値が formula と一致することを検証 ---


def _parametrize_docs_rows():
    """parametrize 用: docs を parse して (label, rho, n, lo, hi) を返す。

    parse 失敗時は collection 時に RuntimeError が伝播して CI 赤になる。
    """
    return _parse_docs_ci_table()


@pytest.mark.parametrize(
    "label,rho,n,expected_lo,expected_hi",
    _parametrize_docs_rows(),
    ids=[row[0] for row in _parametrize_docs_rows()],
)
def test_validation_ci_row_matches_formula(
    label: str,
    rho: float,
    n: int,
    expected_lo: float,
    expected_hi: float,
) -> None:
    """docs/validation.md に記載された CI 値が Fisher z formula で再現できる。

    **この test が pass する条件:** docs の `[lo, hi]` が、同じ行に記載された
    `ρ` と `n` から Fisher z formula で導出した値と一致すること（4 桁丸め）。

    docs 側に typo や stale 値があればこの test が fail する。`REPORTED_CIS`
    のような hardcoded list を経由しないため、docs 単体編集でも検証される。
    """
    lo, hi = fisher_ci(rho, n)
    # 4 桁表示 → 許容誤差 5e-5 (round-to-4 digits 相当)
    assert lo == pytest.approx(expected_lo, abs=5e-5), (
        f"{label}: CI lower bound mismatch. "
        f"docs={expected_lo}, formula(rho={rho}, n={n})={lo:.4f}"
    )
    assert hi == pytest.approx(expected_hi, abs=5e-5), (
        f"{label}: CI upper bound mismatch. "
        f"docs={expected_hi}, formula(rho={rho}, n={n})={hi:.4f}"
    )


# --- 参考値テスト: 主指標 HA48 ΔE (system C) が期待値レンジに収まる ---
# docs の数字が将来変わっても、論文で引用する主指標の「範囲」が壊れたら
# surface させる最終ガード。


def test_ha48_primary_metric_within_paper_range() -> None:
    """HA48 ΔE (system C) ρ が pipeline revision を跨いでも論文レンジに収まる"""
    rows = _parse_docs_ci_table()
    ha48_sys = next(
        (r for r in rows if "HA48" in r[0] and "system C" in r[0] and "L_sem" not in r[0]),
        None,
    )
    assert ha48_sys is not None, "HA48 ΔE (system C) row missing"
    label, rho, n, lo, hi = ha48_sys
    # 論文引用レンジ: 負の相関かつ下端 -0.5 を下回る (既存 doc にも明記)
    assert n == 48
    assert rho < 0, f"{label}: expected negative ρ, got {rho}"
    assert lo < -0.5, f"{label}: CI lower bound should be < -0.5, got {lo}"
