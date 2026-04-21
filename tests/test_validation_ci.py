"""tests/test_validation_ci.py — docs/validation.md §「信頼区間」の再現性テスト

Codex review P2 (PR #104) への恒久対応: docs に記載した Fisher z CI 値が
同じ formula で再現可能であることを CI で保証する。将来、値がずれたら
test が赤になる。
"""
from __future__ import annotations

import math

import pytest


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


# (label, rho, n, expected_lo, expected_hi) — docs/validation.md §信頼区間 と同期
# HA48 ΔE (system C) は current pipeline snapshot (2026-04-21) の値。
# 前版 (Apr 6, pipeline pre-#95): ρ=-0.5195, CI=[-0.7003, -0.2761]。
REPORTED_CIS = [
    ("HA48 ΔE (system C)", -0.4817, 48, -0.6736, -0.2289),
    ("HA48 ΔE (human C, 参照上限)", +0.8616, 48, +0.7647, +0.9204),
    ("HA48 L_sem Phase 5", -0.6020, 48, -0.7567, -0.3835),
    ("HA20 ΔE (system C)", -0.7737, 20, -0.9060, -0.5036),
    ("HA20 ΔE (human C)", -0.9266, 20, -0.9710, -0.8205),
]


@pytest.mark.parametrize(
    "label,rho,n,expected_lo,expected_hi",
    REPORTED_CIS,
    ids=[row[0] for row in REPORTED_CIS],
)
def test_validation_ci_matches_formula(
    label: str,
    rho: float,
    n: int,
    expected_lo: float,
    expected_hi: float,
) -> None:
    """docs に記載した CI 値が Fisher z formula で再現できることを検証"""
    lo, hi = fisher_ci(rho, n)
    # 4 桁表示 → 許容誤差 5e-5 (round-to-4 digits 相当)
    assert lo == pytest.approx(expected_lo, abs=5e-5), (
        f"{label}: CI lower bound mismatch. docs={expected_lo}, formula={lo:.4f}"
    )
    assert hi == pytest.approx(expected_hi, abs=5e-5), (
        f"{label}: CI upper bound mismatch. docs={expected_hi}, formula={hi:.4f}"
    )
