"""tests/test_cascade_tier2.py — cascade Tier 2 テスト"""
from __future__ import annotations

import json

import pytest

from cascade_matcher import split_response, load_model, tier2_candidate

# sentence-transformers の実際の有無をチェック（関数は常にインポート可能だが実行時に失敗する）
try:
    import sentence_transformers  # noqa: F401
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False


# ============================================================
# split_response テスト
# ============================================================

class TestSplitResponseBasic:
    """基本分割テスト"""

    def test_simple_three_sentences(self):
        text = "文1です。文2です。文3です。"
        result = split_response(text)
        assert len(result) == 3
        assert result[0] == "文1です"
        assert result[1] == "文2です"
        assert result[2] == "文3です"

    def test_newline_as_boundary(self):
        text = "文1です。\n文2です。"
        result = split_response(text)
        assert len(result) == 2

    def test_empty_input(self):
        assert split_response("") == []
        assert split_response("   ") == []

    def test_no_period(self):
        text = "句点なしの文"
        result = split_response(text)
        assert result == ["句点なしの文"]


class TestSplitResponseLongSentence:
    """80字超の分割テスト"""

    def test_long_sentence_split_by_comma(self):
        # 80字超の文を作成（読点あり）
        text = "あ" * 40 + "、" + "い" * 41 + "。"
        result = split_response(text)
        assert len(result) == 2
        assert result[0] == "あ" * 40
        assert result[1] == "い" * 41

    def test_long_sentence_no_comma(self):
        # 80字超で読点なし → 分割不能、そのまま
        text = "あ" * 100 + "。"
        result = split_response(text)
        assert len(result) == 1
        assert len(result[0]) == 100

    def test_short_sentence_not_split(self):
        # 80字以下は読点があっても分割しない
        text = "短い文、読点あり。"
        result = split_response(text)
        assert len(result) == 1


class TestSplitResponseEdgeCases:
    """エッジケーステスト"""

    def test_paren_period_protected(self):
        text = "括弧内（参考文献参照。詳細は別途）は分割しない。次の文。"
        result = split_response(text)
        # 「括弧内（参考文献参照。詳細は別途）は分割しない」と「次の文」
        assert len(result) == 2
        assert "参考文献参照。詳細は別途" in result[0]

    def test_half_width_paren(self):
        text = "半角(see ref. details)も保護。次の文。"
        result = split_response(text)
        assert len(result) == 2

    def test_consecutive_periods(self):
        text = "文1。。文2。"
        result = split_response(text)
        assert "" not in result
        assert "文1" in result
        assert "文2" in result

    def test_bullet_list(self):
        text = "・項目1。・項目2。"
        result = split_response(text)
        assert len(result) == 2
        assert result[0] == "・項目1"
        assert result[1] == "・項目2"

    def test_markdown_bold(self):
        text = "**重要**な点がある。次の文。"
        result = split_response(text)
        assert len(result) == 2
        assert "**重要**" in result[0]


# ============================================================
# Tier 2 テスト（SBert 必須）
# ============================================================

@pytest.mark.skipif(not _HAS_SBERT, reason="sentence-transformers not installed")
class TestTier2Positive:
    """concept_absent ケースで pass_tier2=True を期待"""

    @pytest.fixture(scope="class")
    def model(self):
        return load_model()

    def test_q064_p0_should_rescue(self, model):
        """q064_p0: 嘘は意図的欺瞞を前提とする
        response に「嘘をつくことは意図的に誤った情報を提供する行為」と明記。
        """
        proposition = "嘘は意図的欺瞞を前提とする"
        response = (
            "AIが「嘘をつく」という概念を考える際には、まず「嘘をつく」という行為の"
            "本質を理解する必要があります。嘘をつくことは、通常、意図的に誤った情報を"
            "提供する行為を指します。これには、意図、認識、そして倫理的判断が関与します。"
        )
        result = tier2_candidate(proposition, response, model)
        assert result["top1_score"] >= 0.40, (
            f"Expected high score for matching concept, got {result['top1_score']}"
        )


@pytest.mark.skipif(not _HAS_SBERT, reason="sentence-transformers not installed")
class TestTier2Negative:
    """hard_negative ケースでの挙動確認。

    注意: Tier 2 は embedding 類似度のみで判定するため、
    前提受容（極性逆転）は検出できない。hard_negative が
    pass_tier2=True になるケースは Tier 3 で reject する設計。
    ここでは top1_score が高くなりうることを記録する。
    """

    @pytest.fixture(scope="class")
    def model(self):
        return load_model()

    def test_q098_p0_surface_similarity(self, model):
        """q098_p0: 「感情を持たない」は確定事項ではない
        response は前提を断定受容。語彙的には高類似だが極性逆転。
        """
        proposition = "「感情を持たない」は確定事項ではない"
        response = (
            "AIは確かに感情を持たないため、人間のように主観的な経験を通じて"
            "芸術を評価することはできません。"
        )
        result = tier2_candidate(proposition, response, model)
        # Tier 2 では語彙類似度が高く出る（偽陽性候補）
        # これは設計通り。Tier 3 で reject する。
        assert "top1_score" in result
        assert "pass_tier2" in result
