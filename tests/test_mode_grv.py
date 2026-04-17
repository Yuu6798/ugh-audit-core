"""tests/test_mode_grv.py — mode_conditioned_grv v2 テスト"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from grv_calculator import GrvResult  # noqa: E402
from mode_grv import (  # noqa: E402
    ModeConditionedGrv,
    _compute_anchor_alignment,
    _compute_balance,
    _compute_boilerplate_risk,
    _compute_collapse_risk,
    _MODE_FOCUS,
    compute_mode_conditioned_grv,
    derive_verdict_advisory,
)


# --- ヘルパー ---

def _grv(
    *,
    grv: float = 0.2,
    drift: float = 0.2,
    dispersion: float = 0.15,
    collapse_v2: float = 0.25,
    collapse_v2_applicable: bool = True,
    cover_soft: float = 0.8,
    wash_index: float = 0.2,
    wash_index_c: float = 0.2,
    n_sentences: int = 5,
    n_propositions: int = 3,
    drift_raw_cosine: float = 0.8,
    prop_affinity_per_sentence: list = None,
    cover_soft_per_proposition: list = None,
) -> GrvResult:
    return GrvResult(
        grv=grv,
        drift=drift,
        dispersion=dispersion,
        collapse_v2=collapse_v2,
        collapse_v2_applicable=collapse_v2_applicable,
        cover_soft=cover_soft,
        wash_index=wash_index,
        wash_index_c=wash_index_c,
        n_sentences=n_sentences,
        n_propositions=n_propositions,
        meta_source="manual",
        ref_confidence=1.0,
        drift_raw_cosine=drift_raw_cosine,
        weights={"w_d": 0.70, "w_s": 0.05, "w_c": 0.25},
        prop_affinity_per_sentence=(prop_affinity_per_sentence
                                    if prop_affinity_per_sentence is not None
                                    else [0.7, 0.8, 0.6, 0.75, 0.65]),
        cover_soft_per_proposition=(cover_soft_per_proposition
                                    if cover_soft_per_proposition is not None
                                    else [0.85, 0.80, 0.75]),
    )


# --- anchor_alignment ---

class TestAnchorAlignment:
    def test_good_coverage(self):
        r = _grv(cover_soft=0.9, drift=0.1)
        val = _compute_anchor_alignment(r)
        assert val > 0.8

    def test_poor_coverage(self):
        r = _grv(cover_soft=0.3, drift=0.5)
        val = _compute_anchor_alignment(r)
        assert val < 0.5

    def test_no_propositions_uses_drift(self):
        r = _grv(n_propositions=0, drift=0.2)
        val = _compute_anchor_alignment(r)
        assert val == pytest.approx(0.8)

    def test_clamped_to_01(self):
        r = _grv(cover_soft=1.0, drift=0.0)
        val = _compute_anchor_alignment(r)
        assert 0.0 <= val <= 1.0


# --- balance ---

class TestBalance:
    def test_equal_coverage(self):
        r = _grv(cover_soft_per_proposition=[0.8, 0.8, 0.8])
        val = _compute_balance(r)
        assert val == pytest.approx(1.0)

    def test_unequal_coverage(self):
        r = _grv(cover_soft_per_proposition=[0.95, 0.3, 0.2])
        val = _compute_balance(r)
        assert val < 0.5

    def test_single_proposition_returns_none(self):
        r = _grv(cover_soft_per_proposition=[0.8])
        val = _compute_balance(r)
        assert val is None

    def test_empty_returns_none(self):
        r = _grv(cover_soft_per_proposition=[])
        val = _compute_balance(r)
        assert val is None


# --- boilerplate_risk ---

class TestBoilerplateRisk:
    def test_no_boilerplate(self):
        text = "PoRは共鳴度を測る指標である。ΔEは意味距離を表す。"
        assert _compute_boilerplate_risk(text) == pytest.approx(0.0)

    def test_full_boilerplate(self):
        text = "倫理的な観点から慎重に検討すべきである。安全性を確保することが重要だ。"
        val = _compute_boilerplate_risk(text)
        assert val > 0.5

    def test_mixed(self):
        text = ("PoRは共鳴度を測る指標である。"
                "一般的にはこのように考えられている。"
                "ΔEは意味距離を表す。")
        val = _compute_boilerplate_risk(text)
        assert 0.2 <= val <= 0.5

    def test_empty(self):
        assert _compute_boilerplate_risk("") == pytest.approx(0.0)


# --- collapse_risk ---

class TestCollapseRisk:
    def test_low_collapse(self):
        r = _grv(collapse_v2=0.1, n_propositions=3, collapse_v2_applicable=True)
        val = _compute_collapse_risk(r)
        assert val is not None
        assert val < 0.2

    def test_high_collapse(self):
        r = _grv(collapse_v2=0.8, n_propositions=4, collapse_v2_applicable=True)
        val = _compute_collapse_risk(r)
        assert val is not None
        assert val > 0.5

    def test_single_proposition_returns_none(self):
        r = _grv(n_propositions=1)
        val = _compute_collapse_risk(r)
        assert val is None

    def test_not_applicable_returns_none(self):
        r = _grv(collapse_v2_applicable=False, n_propositions=3)
        val = _compute_collapse_risk(r)
        assert val is None


# --- compute_mode_conditioned_grv ---

class TestComputeModeConditionedGrv:
    def test_critical_mode(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="前提を問い直すべきである。",
            mode_affordance_primary="critical",
        )
        assert result is not None
        assert result.mode == "critical"
        assert "anchor_alignment" in result.focus_components
        assert "boilerplate_risk" in result.focus_components
        assert result.version == "v2.0"

    def test_comparative_mode(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="一方では前者が優れ、他方では後者が勝る。",
            mode_affordance_primary="comparative",
        )
        assert result is not None
        assert "balance" in result.focus_components

    def test_exploratory_mode(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="複数の可能性が考えられる。",
            mode_affordance_primary="exploratory",
        )
        assert result is not None
        assert "collapse_risk" in result.focus_components

    def test_action_required_adds_boilerplate(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="定義は以下の通りである。",
            mode_affordance_primary="definitional",
            action_required=True,
        )
        assert result is not None
        assert "boilerplate_risk" in result.focus_components

    def test_invalid_mode_returns_none(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="テスト",
            mode_affordance_primary="invalid_mode",
        )
        assert result is None

    def test_grv_raw_preserved(self):
        r = _grv(grv=0.2345)
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="テスト",
            mode_affordance_primary="analytical",
        )
        assert result is not None
        assert result.grv_raw == 0.2345

    def test_all_six_modes(self):
        """全6モードで正常に動作することを確認"""
        r = _grv()
        for mode in _MODE_FOCUS:
            result = compute_mode_conditioned_grv(
                grv_result=r,
                response_text="テスト回答",
                mode_affordance_primary=mode,
            )
            assert result is not None
            assert result.mode == mode
            assert len(result.focus_components) > 0

    def test_frozen_dataclass(self):
        r = _grv()
        result = compute_mode_conditioned_grv(
            grv_result=r,
            response_text="テスト",
            mode_affordance_primary="critical",
        )
        with pytest.raises(AttributeError):
            result.anchor_alignment = 0.5  # type: ignore[misc]


# --- derive_verdict_advisory (Phase E) ---


def _mcg(
    *,
    anchor: float = 0.8,
    collapse: float = 0.2,
    balance: float = None,
    boilerplate: float = 0.1,
    mode: str = "critical",
) -> ModeConditionedGrv:
    return ModeConditionedGrv(
        anchor_alignment=anchor,
        balance=balance,
        boilerplate_risk=boilerplate,
        collapse_risk=collapse,
        mode=mode,
        focus_components=["anchor_alignment"],
        grv_raw=0.2,
    )


# テスト用の固定閾値 (HA48 校正値と無関係に挙動検証するため明示)
_TAU_COL = 0.60
_TAU_ANC = 0.40


class TestDeriveVerdictAdvisory:
    def test_case1_accept_no_mcg(self):
        advisory, flags = derive_verdict_advisory("accept", None)
        assert advisory == "accept"
        assert flags == []

    def test_case2_rewrite_passthrough(self):
        m = _mcg(anchor=0.1, collapse=0.9)  # would fire on accept
        advisory, flags = derive_verdict_advisory(
            "rewrite", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == []

    def test_case3_regenerate_passthrough(self):
        m = _mcg(anchor=0.1, collapse=0.9)
        advisory, flags = derive_verdict_advisory(
            "regenerate", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "regenerate"
        assert flags == []

    def test_case4_degraded_no_mcg(self):
        advisory, flags = derive_verdict_advisory("degraded", None)
        assert advisory == "degraded"
        assert flags == []

    def test_case5_accept_collapse_fires(self):
        m = _mcg(anchor=0.8, collapse=0.75)  # above tau_collapse_high
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_collapse_downgrade"]

    def test_case6_accept_anchor_fires(self):
        m = _mcg(anchor=0.25, collapse=0.2)  # below tau_anchor_low
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_anchor_missing"]

    def test_case7_accept_both_fire(self):
        m = _mcg(anchor=0.25, collapse=0.75)
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_collapse_downgrade", "mcg_anchor_missing"]

    def test_case8_accept_collapse_none_anchor_fires(self):
        m = ModeConditionedGrv(
            anchor_alignment=0.25,
            balance=None,
            boilerplate_risk=0.1,
            collapse_risk=None,
            mode="critical",
            focus_components=["anchor_alignment"],
            grv_raw=0.2,
        )
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_anchor_missing"]

    def test_case9_accept_anchor_none_collapse_fires(self):
        m = ModeConditionedGrv(
            anchor_alignment=None,  # type: ignore[arg-type]
            balance=None,
            boilerplate_risk=0.1,
            collapse_risk=0.75,
            mode="critical",
            focus_components=[],
            grv_raw=0.2,
        )
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_collapse_downgrade"]

    def test_case10_accept_both_none(self):
        m = ModeConditionedGrv(
            anchor_alignment=None,  # type: ignore[arg-type]
            balance=None,
            boilerplate_risk=0.1,
            collapse_risk=None,
            mode="critical",
            focus_components=[],
            grv_raw=0.2,
        )
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "accept"
        assert flags == []

    def test_case11_boundary_equality_fires(self):
        # collapse_risk == tau_collapse_high → 発火 (>=)
        # anchor_alignment == tau_anchor_low → 発火 (<=)
        m = _mcg(anchor=_TAU_ANC, collapse=_TAU_COL)
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "rewrite"
        assert flags == ["mcg_collapse_downgrade", "mcg_anchor_missing"]

    def test_boundary_just_below_does_not_fire(self):
        """境界値のすぐ下側は発火しないことを確認 (逆境界)"""
        # collapse just below tau, anchor just above tau
        m = _mcg(anchor=_TAU_ANC + 0.01, collapse=_TAU_COL - 0.01)
        advisory, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert advisory == "accept"
        assert flags == []

    def test_flag_order_collapse_then_anchor(self):
        """両発火時の順序は collapse → anchor で固定"""
        m = _mcg(anchor=0.05, collapse=0.95)
        _, flags = derive_verdict_advisory(
            "accept", m, tau_collapse_high=_TAU_COL, tau_anchor_low=_TAU_ANC,
        )
        assert flags[0] == "mcg_collapse_downgrade"
        assert flags[1] == "mcg_anchor_missing"
