"""compute_quality_score() のテスト

Model C'（ボトルネック型）quality_score の正確性・フォールバック・回帰を検証する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from detector import (
    QUALITY_ALPHA,
    QUALITY_BETA,
    QUALITY_GAMMA,
    QUALITY_MODEL_NAME,
    compute_quality_score,
)


def test_params_defined():
    """α, β, γ が float で 0.0〜2.0 の範囲であること"""
    for name, val in [("alpha", QUALITY_ALPHA), ("beta", QUALITY_BETA), ("gamma", QUALITY_GAMMA)]:
        assert isinstance(val, float), f"{name} is not float: {type(val)}"
        assert 0.0 <= val <= 2.0, f"{name} out of range: {val}"

    assert isinstance(QUALITY_MODEL_NAME, str)
    assert QUALITY_MODEL_NAME == "bottleneck_v1"


def test_bottleneck_behavior():
    """fail_max=1.0 → hit_rate=1.0 でも quality_score ≤ 2.0"""
    result = compute_quality_score(
        propositions_hit_rate=1.0,
        fail_max=1.0,
        delta_e_full=0.0,
    )
    assert result["quality_score"] <= 2.0, f"ボトルネック不発動: {result['quality_score']}"
    assert result["quality_score"] == 1.0, f"fail_max=1.0 で 1.0 にならない: {result['quality_score']}"
    assert result["quality_model"] == "bottleneck_v1"


def test_fallback():
    """fail_max=None → L_struct=0.0 として算出（ボトルネック不発動）"""
    hit_rate = 0.8
    delta_e = 0.2

    result_fallback = compute_quality_score(
        propositions_hit_rate=hit_rate,
        fail_max=None,
        delta_e_full=delta_e,
    )

    expected = 5 - 4 * (0.4 * (1 - hit_rate) + 0.8 * delta_e)
    assert abs(result_fallback["quality_score"] - expected) < 0.01, \
        f"フォールバック値不一致: got {result_fallback['quality_score']}, expected {expected}"

    result_explicit = compute_quality_score(
        propositions_hit_rate=hit_rate,
        fail_max=0.0,
        delta_e_full=delta_e,
    )
    assert abs(result_fallback["quality_score"] - result_explicit["quality_score"]) < 0.001


def test_hit_rate_unchanged():
    """compute_quality_score は propositions_hit_rate を変更しない"""
    original_hit_rate = 0.6667

    result = compute_quality_score(
        propositions_hit_rate=original_hit_rate,
        fail_max=0.5,
        delta_e_full=0.3,
    )

    if "propositions_hit_rate" in result:
        assert result["propositions_hit_rate"] == original_hit_rate

    assert "quality_score" in result
    assert result["quality_score"] != original_hit_rate


def test_ha20_regression():
    """HA20 の 20 件の quality_score が検証時の値と ±0.05 以内で一致"""
    import csv

    csv_path = Path(__file__).resolve().parent.parent / "analysis/semantic_loss/case_analysis_model_c.csv"
    if not csv_path.exists():
        pytest.skip("case_analysis_model_c.csv not found")

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 20, f"Expected 20 rows, got {len(rows)}"

    for row in rows:
        result = compute_quality_score(
            propositions_hit_rate=float(row["system_hit_rate"]),
            fail_max=float(row["fail_max"]),
            delta_e_full=float(row["delta_e_full"]),
        )
        expected = float(row["model_c_prime_pred"])
        actual = result["quality_score"]
        assert abs(actual - expected) <= 0.05, \
            f"{row['id']}: got {actual:.3f}, expected {expected:.3f} (diff={actual-expected:.3f})"
