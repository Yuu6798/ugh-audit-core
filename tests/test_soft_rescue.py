"""
tests/test_soft_rescue.py
soft_rescue モジュールの単体テスト
"""
from __future__ import annotations

from ugh_audit.soft_rescue import maybe_build_soft_rescue


def _base_kwargs(**overrides):
    """デフォルトのキーワード引数 (全ガード条件を満たす状態)"""
    defaults = {
        "question": "UGHer とは何ですか？",
        "response": "UGHer は無意識的重力仮説です。意味的誠実性を測定します。",
        "question_meta": {
            "core_propositions": ["UGHer は無意識的重力仮説である"],
            "metadata_confidence": 0.9,
        },
        "mode": "computed_ai_draft",
        "metadata_confidence": 0.9,
        "S": 0.95,
        "C": 0.0,
        "f2": 0.0,
        "f3": 0.0,
    }
    defaults.update(overrides)
    return defaults


class TestGuardConditions:
    """各ガード条件が正しく None を返すことを検証"""

    def test_wrong_mode(self):
        assert maybe_build_soft_rescue(**_base_kwargs(mode="computed")) is None

    def test_no_question_meta(self):
        assert maybe_build_soft_rescue(**_base_kwargs(question_meta=None)) is None

    def test_c_not_zero(self):
        assert maybe_build_soft_rescue(**_base_kwargs(C=0.5)) is None

    def test_s_too_low(self):
        assert maybe_build_soft_rescue(**_base_kwargs(S=0.5)) is None

    def test_s_none(self):
        assert maybe_build_soft_rescue(**_base_kwargs(S=None)) is None

    def test_low_confidence(self):
        assert maybe_build_soft_rescue(**_base_kwargs(metadata_confidence=0.3)) is None

    def test_f2_nonzero(self):
        assert maybe_build_soft_rescue(**_base_kwargs(f2=0.5)) is None

    def test_f3_at_one(self):
        assert maybe_build_soft_rescue(**_base_kwargs(f3=1.0)) is None

    def test_empty_propositions(self):
        meta = {"core_propositions": [], "metadata_confidence": 0.9}
        assert maybe_build_soft_rescue(**_base_kwargs(question_meta=meta)) is None


class TestRescueOutput:
    """rescue が成功した場合の出力構造を検証"""

    def test_successful_rescue_structure(self):
        result = maybe_build_soft_rescue(**_base_kwargs())
        assert result is not None
        assert result["type"] == "ai_draft_c_floor"
        assert isinstance(result["target_proposition_index"], int)
        assert isinstance(result["target_proposition"], str)
        assert isinstance(result["evidence_span"], str)
        assert isinstance(result["confidence"], float)
        assert result["confidence"] >= 0.08

    def test_no_match_returns_none(self):
        """回答が命題と全く関係ない場合は None"""
        result = maybe_build_soft_rescue(**_base_kwargs(
            response="AAAA BBBB CCCC DDDD",
            question_meta={
                "core_propositions": ["XXXX YYYY ZZZZ"],
                "metadata_confidence": 0.9,
            },
        ))
        assert result is None

    def test_f3_below_one_passes(self):
        """f3 < 1.0 はガードを通過する"""
        result = maybe_build_soft_rescue(**_base_kwargs(f3=0.5))
        assert result is not None
