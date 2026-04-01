"""tests/test_cascade_tier3.py — cascade Tier 3 多条件フィルタテスト"""
from __future__ import annotations


from cascade_matcher import check_atomic_alignment, tier3_filter


# ============================================================
# check_atomic_alignment テスト
# ============================================================

class TestAtomicAlignmentPositive:
    """整合ありのケース"""

    def test_exact_match(self):
        atomics = ["戦争閾値|低下する"]
        sentence = "戦争のハードルを下げる可能性があります。戦争閾値が低下するリスク"
        result = check_atomic_alignment(atomics, sentence)
        assert result["pass"] is True
        assert result["aligned_count"] >= 1

    def test_multiple_atomics_one_aligned(self):
        atomics = ["嘘|意図的欺瞞を前提とする", "AIの出力|証言として扱える"]
        sentence = "嘘をつくことは意図的に誤った情報を提供する、つまり意図的欺瞞を前提とする行為です"
        result = check_atomic_alignment(atomics, sentence)
        assert result["pass"] is True
        assert result["aligned_count"] >= 1

    def test_substring_match(self):
        """3文字以上の部分文字列一致"""
        atomics = ["ブラックボックス|部分的照明にとどまる"]
        sentence = "ブラックボックス問題の部分的な照明にとどまっている"
        result = check_atomic_alignment(atomics, sentence)
        assert result["pass"] is True


class TestAtomicAlignmentNegative:
    """整合なしのケース"""

    def test_no_match(self):
        atomics = ["量子コンピュータ|暗号を解読する"]
        sentence = "AIは倫理的な判断を行う能力を持たない"
        result = check_atomic_alignment(atomics, sentence)
        assert result["pass"] is False
        assert result["aligned_count"] == 0

    def test_left_only_match(self):
        """左辺のみ一致、右辺不一致"""
        atomics = ["戦争閾値|量子的に変化する"]
        sentence = "戦争閾値が低下するリスクがある"
        result = check_atomic_alignment(atomics, sentence)
        assert result["pass"] is False
        units = result["aligned_units"]
        assert units[0]["left_match"] is True
        assert units[0]["right_match"] is False

    def test_empty_sentence(self):
        atomics = ["嘘|意図的欺瞞を前提とする"]
        result = check_atomic_alignment(atomics, "")
        assert result["pass"] is False

    def test_empty_atomics(self):
        result = check_atomic_alignment([], "何かの文")
        assert result["pass"] is False


class TestAtomicAlignmentSynonym:
    """synonym 展開での整合"""

    def test_synonym_expansion(self):
        atomics = ["検証の方法|未確立である"]
        sentence = "確認の方法が未確立のままである"
        # "検証" → "確認" が synonym_dict にある → "確認の方法" が sentence に含まれる
        syn = {"検証": ["確認", "実証"]}
        result = check_atomic_alignment(atomics, sentence, synonym_dict=syn)
        assert result["pass"] is True

    def test_synonym_no_help(self):
        """synonym があっても右辺が一致しないケース"""
        atomics = ["検証手段|量子的に変化する"]
        sentence = "確認する方法がある"
        syn = {"検証": ["確認"]}
        result = check_atomic_alignment(atomics, sentence, synonym_dict=syn)
        assert result["pass"] is False


# ============================================================
# tier3_filter テスト
# ============================================================

def _make_tier2_result(top1_score=0.65, gap=0.10, top1_sentence="テスト文"):
    return {
        "top1_sentence": top1_sentence,
        "top1_score": top1_score,
        "top2_score": top1_score - gap,
        "gap": gap,
        "all_scores": [top1_score, top1_score - gap],
        "pass_tier2": True,
    }


class TestTier3AllPass:
    """全条件 pass → Z_RESCUED"""

    def test_all_conditions_met(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.15,
            top1_sentence="戦争閾値が低下するリスクがある"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=False,
            f4_flag=0.0,
            atomic_units=["戦争閾値|低下する"],
        )
        assert result["verdict"] == "Z_RESCUED"
        assert all(result["conditions"].values())
        assert result["fail_reason"] is None


class TestTier3F4Block:
    """f4 発火で miss"""

    def test_f4_half(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.15,
            top1_sentence="戦争閾値が低下するリスクがある"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=False,
            f4_flag=0.5,
            atomic_units=["戦争閾値|低下する"],
        )
        assert result["verdict"] == "miss"
        assert result["conditions"]["c4_f4_clear"] is False
        assert "f4_flag" in result["fail_reason"]

    def test_f4_full(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.15,
            top1_sentence="戦争閾値が低下するリスクがある"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=False,
            f4_flag=1.0,
            atomic_units=["戦争閾値|低下する"],
        )
        assert result["verdict"] == "miss"
        assert result["conditions"]["c4_f4_clear"] is False


class TestTier3AtomicFail:
    """atomic 不整合で miss"""

    def test_no_atomic_alignment(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.15,
            top1_sentence="AIは倫理的な判断を行う能力を持たない"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=False,
            f4_flag=0.0,
            atomic_units=["量子コンピュータ|暗号を解読する"],
        )
        assert result["verdict"] == "miss"
        assert result["conditions"]["c5_atomic"] is False
        assert "atomic" in result["fail_reason"]


class TestTier3GapFail:
    """gap 不足で miss"""

    def test_gap_below_threshold(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.02,
            top1_sentence="戦争閾値が低下するリスクがある"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=False,
            f4_flag=0.0,
            atomic_units=["戦争閾値|低下する"],
        )
        assert result["verdict"] == "miss"
        assert result["conditions"]["c3_gap"] is False
        assert "gap" in result["fail_reason"]


class TestTier3TfidfDuplicate:
    """Tier 1 already hit → c1 fail"""

    def test_tier1_hit_blocks(self):
        t2 = _make_tier2_result(
            top1_score=0.70, gap=0.15,
            top1_sentence="戦争閾値が低下するリスクがある"
        )
        result = tier3_filter(
            tier2_result=t2,
            tier1_hit=True,
            f4_flag=0.0,
            atomic_units=["戦争閾値|低下する"],
        )
        assert result["verdict"] == "miss"
        assert result["conditions"]["c1_tfidf_miss"] is False
