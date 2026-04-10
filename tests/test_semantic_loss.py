"""tests/test_semantic_loss.py — 意味損失関数のテスト (Phase 1)"""
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
# L_total (重み付き合計)
# =========================================================

class TestTotal:
    def test_only_LQ_available(self):
        """命題なし → L_P=None, L_X=None, L_Q のみ有効"""
        loss = compute_semantic_loss(_ev(f3_operator=0.8, propositions_total=0))
        # L_Q=0.8 のみ → L_total = 0.8
        assert loss.L_total == pytest.approx(0.8)

    def test_LP_and_LQ_normalized(self):
        ev = _ev(propositions_hit=2, propositions_total=4, f3_operator=0.5)
        loss = compute_semantic_loss(ev)
        # L_P=0.5, L_Q=0.5, L_X=None → weights L_P=0.40, L_Q=0.15
        w_sum = 0.40 + 0.15
        expected = (0.40 / w_sum) * 0.5 + (0.15 / w_sum) * 0.5
        assert loss.L_total == pytest.approx(expected, abs=1e-4)

    def test_all_three_components(self):
        ev = _ev(
            propositions_hit=1,
            propositions_total=2,
            f3_operator=0.0,
            hit_ids=[0],
            miss_ids=[1],
            hit_sources={0: "tfidf", 1: "miss"},
        )
        props = ["AIは便利である", "低ΔEは良い回答を保証しない"]
        loss = compute_semantic_loss(ev, propositions=props)
        # L_P=0.5, L_Q=0.0, L_X=0.5
        w_sum = 0.40 + 0.15 + 0.30
        expected = (0.40 / w_sum) * 0.5 + (0.15 / w_sum) * 0.0 + (0.30 / w_sum) * 0.5
        assert loss.L_total == pytest.approx(expected, abs=1e-4)

    def test_custom_weights(self):
        ev = _ev(propositions_hit=1, propositions_total=2, f3_operator=0.0)
        custom = {"L_P": 1.0, "L_Q": 0.0, "L_R": 0.0, "L_A": 0.0, "L_G": 0.0, "L_X": 0.0}
        loss = compute_semantic_loss(ev, weights=custom)
        # L_P=0.5, L_Q=0.0, L_X=None → weight L_P=1.0, L_Q=0.0 → total=0.5
        assert loss.L_total == pytest.approx(0.5)

    def test_zero_loss(self):
        ev = _ev(propositions_hit=3, propositions_total=3, f3_operator=0.0)
        loss = compute_semantic_loss(ev)
        assert loss.L_total == pytest.approx(0.0)


# =========================================================
# Phase 2/3 stubs
# =========================================================

class TestPhaseStubs:
    def test_phase2_stubs_are_none(self):
        loss = compute_semantic_loss(_ev())
        assert loss.L_R is None
        assert loss.L_A is None

    def test_phase3_stub_is_none(self):
        loss = compute_semantic_loss(_ev())
        assert loss.L_G is None


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
