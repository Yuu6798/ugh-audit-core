"""tests/test_semantic_loss.py — 意味損失関数のテスト (Phase 1-3)"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from semantic_loss import compute_semantic_loss  # noqa: E402
from ugh_calculator import Evidence  # noqa: E402


# --- ヘルパー ---

def _ev(**kwargs) -> Evidence:
    defaults = {"question_id": "test001"}
    defaults.update(kwargs)
    return Evidence(**defaults)


# =========================================================
# L_P (命題損失)
# =========================================================

class TestLP:
    def test_full_coverage(self):
        loss = compute_semantic_loss(_ev(propositions_hit=3, propositions_total=3))
        assert loss.L_P == pytest.approx(0.0)

    def test_no_coverage(self):
        loss = compute_semantic_loss(_ev(propositions_hit=0, propositions_total=3))
        assert loss.L_P == pytest.approx(1.0)

    def test_partial_coverage(self):
        loss = compute_semantic_loss(_ev(propositions_hit=2, propositions_total=5))
        assert loss.L_P == pytest.approx(0.6)

    def test_no_propositions_returns_none(self):
        loss = compute_semantic_loss(_ev(propositions_total=0))
        assert loss.L_P is None


# =========================================================
# L_Q (制約損失)
# =========================================================

class TestLQ:
    def test_no_operator_loss(self):
        assert compute_semantic_loss(_ev(f3_operator=0.0)).L_Q == pytest.approx(0.0)

    def test_full_operator_loss(self):
        assert compute_semantic_loss(_ev(f3_operator=1.0)).L_Q == pytest.approx(1.0)

    def test_partial_operator_loss(self):
        assert compute_semantic_loss(_ev(f3_operator=0.5)).L_Q == pytest.approx(0.5)


# =========================================================
# L_X (極性反転損失)
# =========================================================

class TestLX:
    def test_no_propositions_total_returns_none(self):
        loss = compute_semantic_loss(_ev(propositions_total=0))
        assert loss.L_X is None

    def test_no_propositions_text_returns_none(self):
        loss = compute_semantic_loss(
            _ev(propositions_hit=1, propositions_total=2, miss_ids=[1])
        )
        assert loss.L_X is None

    def test_miss_with_polarity_flip(self):
        ev = _ev(
            propositions_hit=1,
            propositions_total=2,
            hit_ids=[0],
            miss_ids=[1],
            hit_sources={0: "tfidf", 1: "miss"},
        )
        props = ["AIは便利である", "低ΔEは良い回答を保証しない"]
        loss = compute_semantic_loss(ev, propositions=props)
        # idx=1 "保証しない" → negation family (polarity_flip) → 1 miss / 2 total
        assert loss.L_X == pytest.approx(0.5)

    def test_miss_without_polarity_flip(self):
        ev = _ev(
            propositions_hit=1,
            propositions_total=2,
            hit_ids=[0],
            miss_ids=[1],
            hit_sources={0: "tfidf", 1: "miss"},
        )
        props = ["AIは便利である", "技術は進歩している"]
        loss = compute_semantic_loss(ev, propositions=props)
        assert loss.L_X == pytest.approx(0.0)

    def test_all_hit_no_polarity_loss(self):
        ev = _ev(
            propositions_hit=2,
            propositions_total=2,
            hit_ids=[0, 1],
            miss_ids=[],
            hit_sources={0: "tfidf", 1: "tfidf"},
        )
        props = ["保証しない理由がある", "限界は存在する"]
        loss = compute_semantic_loss(ev, propositions=props)
        assert loss.L_X == pytest.approx(0.0)


# =========================================================
# L_R (参照安定性損失)
# =========================================================

class TestLR:
    def test_no_premise_loss(self):
        assert compute_semantic_loss(_ev(f4_premise=0.0)).L_R == pytest.approx(0.0)

    def test_full_premise_acceptance(self):
        assert compute_semantic_loss(_ev(f4_premise=1.0)).L_R == pytest.approx(1.0)

    def test_partial_premise_acceptance(self):
        assert compute_semantic_loss(_ev(f4_premise=0.5)).L_R == pytest.approx(0.5)

    def test_f4_none_returns_none(self):
        loss = compute_semantic_loss(_ev(f4_premise=None))
        assert loss.L_R is None


# =========================================================
# L_A (曖昧性増大損失)
# =========================================================

class TestLA:
    def test_no_topic_drift(self):
        assert compute_semantic_loss(_ev(f1_anchor=0.0)).L_A == pytest.approx(0.0)

    def test_full_topic_drift(self):
        assert compute_semantic_loss(_ev(f1_anchor=1.0)).L_A == pytest.approx(1.0)

    def test_partial_topic_drift(self):
        assert compute_semantic_loss(_ev(f1_anchor=0.5)).L_A == pytest.approx(0.5)


# =========================================================
# L_total (重み付き合計)
# =========================================================

class TestTotal:
    def test_all_flags_zero(self):
        """全 f-flag=0, 命題全 hit → L_total=0"""
        ev = _ev(propositions_hit=3, propositions_total=3, f3_operator=0.0)
        loss = compute_semantic_loss(ev)
        assert loss.L_total == pytest.approx(0.0)

    def test_degraded_no_propositions(self):
        """命題なし, f4=None → L_P=None, L_X=None, L_R=None; L_Q, L_A, L_F のみ"""
        loss = compute_semantic_loss(
            _ev(
                f3_operator=0.8, f1_anchor=0.4, f2_unknown=1.0,
                f4_premise=None, propositions_total=0,
            )
        )
        # 有効: L_Q=0.8 (w=0.02), L_A=0.4 (w=0.02), L_F=1.0 (w=0.35)
        w_sum = 0.02 + 0.02 + 0.35
        expected = (0.02 / w_sum) * 0.8 + (0.02 / w_sum) * 0.4 + (0.35 / w_sum) * 1.0
        assert loss.L_total == pytest.approx(expected, abs=1e-4)

    def test_six_components_without_grv_x(self):
        """L_P, L_Q, L_R, L_A, L_F 全有効 (L_X=None, L_G=None)"""
        ev = _ev(
            propositions_hit=2, propositions_total=4,
            f3_operator=0.5, f4_premise=0.5, f1_anchor=1.0, f2_unknown=0.0,
        )
        loss = compute_semantic_loss(ev)
        # L_P=0.5, L_Q=0.5, L_R=0.5, L_A=1.0, L_F=0.0
        w_sum = 0.25 + 0.02 + 0.08 + 0.02 + 0.35
        expected = (
            (0.25 / w_sum) * 0.5
            + (0.02 / w_sum) * 0.5
            + (0.08 / w_sum) * 0.5
            + (0.02 / w_sum) * 1.0
            + (0.35 / w_sum) * 0.0
        )
        assert loss.L_total == pytest.approx(expected, abs=1e-4)

    def test_all_components_with_grv(self):
        """L_P, L_Q, L_R, L_A, L_G, L_F 全有効 (L_X=None)"""
        ev = _ev(
            propositions_hit=2, propositions_total=4,
            f3_operator=0.5, f4_premise=0.5, f1_anchor=0.0, f2_unknown=0.5,
        )
        loss = compute_semantic_loss(ev, grv=0.4)
        # L_P=0.5, L_Q=0.5, L_R=0.5, L_A=0.0, L_G=0.4, L_F=0.5
        w_sum = 0.25 + 0.02 + 0.08 + 0.02 + 0.13 + 0.35
        expected = (
            (0.25 / w_sum) * 0.5
            + (0.02 / w_sum) * 0.5
            + (0.08 / w_sum) * 0.5
            + (0.02 / w_sum) * 0.0
            + (0.13 / w_sum) * 0.4
            + (0.35 / w_sum) * 0.5
        )
        assert loss.L_total == pytest.approx(expected, abs=1e-4)

    def test_custom_weights(self):
        ev = _ev(propositions_hit=1, propositions_total=2, f3_operator=0.0)
        custom = {
            "L_P": 1.0, "L_Q": 0.0, "L_R": 0.0, "L_A": 0.0,
            "L_G": 0.0, "L_F": 0.0, "L_X": 0.0,
        }
        loss = compute_semantic_loss(ev, weights=custom)
        assert loss.L_total == pytest.approx(0.5)


# =========================================================
# L_G (因果構造損失 / grv)
# =========================================================

class TestLG:
    def test_grv_not_provided_returns_none(self):
        loss = compute_semantic_loss(_ev())
        assert loss.L_G is None

    def test_grv_zero(self):
        loss = compute_semantic_loss(_ev(), grv=0.0)
        assert loss.L_G == pytest.approx(0.0)

    def test_grv_one(self):
        loss = compute_semantic_loss(_ev(), grv=1.0)
        assert loss.L_G == pytest.approx(1.0)

    def test_grv_mid(self):
        loss = compute_semantic_loss(_ev(), grv=0.3)
        assert loss.L_G == pytest.approx(0.3)

    def test_grv_contributes_to_total(self):
        """grv 提供時は L_G が L_total に寄与する"""
        ev = _ev(propositions_hit=3, propositions_total=3, f3_operator=0.0)
        loss_without = compute_semantic_loss(ev)
        loss_with = compute_semantic_loss(ev, grv=0.5)
        assert loss_without.L_G is None
        assert loss_with.L_G == pytest.approx(0.5)
        assert loss_with.L_total > loss_without.L_total


# =========================================================
# L_F (用語捏造損失 - Phase 4)
# =========================================================

class TestLF:
    def test_no_fabrication(self):
        assert compute_semantic_loss(_ev(f2_unknown=0.0)).L_F == pytest.approx(0.0)

    def test_full_fabrication(self):
        assert compute_semantic_loss(_ev(f2_unknown=1.0)).L_F == pytest.approx(1.0)

    def test_partial_fabrication(self):
        assert compute_semantic_loss(_ev(f2_unknown=0.5)).L_F == pytest.approx(0.5)

    def test_L_F_dominates_default_weight(self):
        """L_F のデフォルト重み (0.35) が最大であることを確認"""
        from semantic_loss import DEFAULT_WEIGHTS
        assert DEFAULT_WEIGHTS["L_F"] == max(DEFAULT_WEIGHTS.values())


# =========================================================
# SemanticLoss dataclass
# =========================================================

class TestDataclass:
    def test_frozen(self):
        loss = compute_semantic_loss(_ev())
        with pytest.raises(AttributeError):
            loss.L_Q = 0.5  # type: ignore[misc]

    def test_weights_used_populated(self):
        loss = compute_semantic_loss(
            _ev(propositions_hit=1, propositions_total=2, f3_operator=0.5)
        )
        assert "L_P" in loss.weights_used
        assert "L_Q" in loss.weights_used
        assert sum(loss.weights_used.values()) == pytest.approx(1.0)
