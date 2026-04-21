"""tests/test_hit_sources.py — summarize_hit_sources 単体テスト

Paper defense: core pipeline (tfidf) vs cascade layer (cascade_rescued) の
分離を API 出力 JSON に surfacing するヘルパーの契約を固定する。
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from ugh_calculator import summarize_hit_sources  # noqa: E402


class TestSummarizeHitSources:
    def test_empty_propositions_returns_none(self):
        """命題総数 0 のとき None (API 側は null) を返す"""
        assert summarize_hit_sources({}, 0) is None
        assert summarize_hit_sources({}, -1) is None

    def test_all_tfidf_hits(self):
        result = summarize_hit_sources(
            {0: "tfidf", 1: "tfidf", 2: "tfidf"}, 3,
        )
        assert result is not None
        assert result["core_hit"] == 3
        assert result["cascade_rescued"] == 0
        assert result["miss"] == 0
        assert result["total"] == 3
        assert result["core_only_hit_rate"] == "3/3"
        assert result["per_proposition"] == {"0": "tfidf", "1": "tfidf", "2": "tfidf"}

    def test_mixed_core_cascade_miss(self):
        result = summarize_hit_sources(
            {0: "tfidf", 1: "cascade_rescued", 2: "miss"}, 3,
        )
        assert result is not None
        assert result["core_hit"] == 1
        assert result["cascade_rescued"] == 1
        assert result["miss"] == 1
        assert result["total"] == 3
        # 決定性主張の分子は tfidf-only
        assert result["core_only_hit_rate"] == "1/3"

    def test_all_miss(self):
        result = summarize_hit_sources(
            {0: "miss", 1: "miss"}, 2,
        )
        assert result is not None
        assert result["core_hit"] == 0
        assert result["cascade_rescued"] == 0
        assert result["miss"] == 2
        assert result["core_only_hit_rate"] == "0/2"

    def test_per_proposition_keys_are_strings(self):
        """per_proposition の key は JSON 互換のため str に変換されている"""
        result = summarize_hit_sources({0: "tfidf", 5: "miss"}, 6)
        assert result is not None
        for k in result["per_proposition"]:
            assert isinstance(k, str)
        assert "0" in result["per_proposition"]
        assert "5" in result["per_proposition"]

    def test_total_and_hit_sources_length_may_differ(self):
        """propositions_total は全命題数、hit_sources は検出済み命題のみ。

        この食い違いは発生しないはずだが、発生しても core_only_hit_rate の
        分母は propositions_total を信頼源とする契約を固定する。
        """
        # hit_sources に 2 件しかないが、propositions_total は 3
        result = summarize_hit_sources({0: "tfidf", 1: "miss"}, 3)
        assert result is not None
        # 合計は hit_sources 集計値 = 2（3 ではない）
        assert result["core_hit"] + result["cascade_rescued"] + result["miss"] == 2
        # ただし total は propositions_total = 3
        assert result["total"] == 3
        # core_only_hit_rate は total=3 を分母とする
        assert result["core_only_hit_rate"] == "1/3"

    def test_cascade_only_hits(self):
        """cascade_rescued だけで core_hit=0 のケース (= 決定性主張では N=0)"""
        result = summarize_hit_sources(
            {0: "cascade_rescued", 1: "cascade_rescued"}, 2,
        )
        assert result is not None
        assert result["core_hit"] == 0
        assert result["cascade_rescued"] == 2
        # 論文の決定性主張は 0 件で支持されないことが明示される
        assert result["core_only_hit_rate"] == "0/2"
