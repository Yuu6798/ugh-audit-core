"""tests/test_hit_sources.py — summarize_hit_sources 単体テスト

Paper defense: core pipeline (tfidf) vs cascade layer (cascade_rescued) の
分離を API 出力 JSON に surfacing するヘルパーの契約を固定する。
"""
from __future__ import annotations

import sys

import pytest

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

    def test_incomplete_mapping_preserves_internal_consistency(self):
        """hit_sources が total を下回る mapping のとき、miss は total から derive される。

        Codex review P2 (PR #104): 以前は miss を explicit "miss" label の
        件数で数えていたため、mapping が total 未満だと core + cascade +
        miss < total の内部不整合が発生していた。本 test は miss が total
        から derive されることで常に合計が一致する契約を固定する。
        """
        # hit_sources に 2 件しかないが、propositions_total は 3
        result = summarize_hit_sources({0: "tfidf", 1: "miss"}, 3)
        assert result is not None
        # 内部整合: core + cascade + miss == total
        assert (
            result["core_hit"] + result["cascade_rescued"] + result["miss"]
            == result["total"]
        )
        # 具体値: explicit tfidf=1, explicit cascade=0, derived miss = 3-1-0 = 2
        assert result["core_hit"] == 1
        assert result["cascade_rescued"] == 0
        assert result["miss"] == 2  # derived: unmapped 1件 + explicit miss 1件
        assert result["total"] == 3
        # core_only_hit_rate は total=3 を分母とする (変更なし)
        assert result["core_only_hit_rate"] == "1/3"

    def test_empty_mapping_with_nonzero_total_treats_all_as_miss(self):
        """compat fallback: hit_sources={} + total>0 で全て miss 扱い。

        Codex review P2 (PR #104): server.py の compat fallback で
        `evidence.hit_sources` 属性が欠落した旧 Evidence に対し `{}` を
        渡すケース。以前は miss=0 が返り miss rate を過小報告していた。
        """
        result = summarize_hit_sources({}, 5)
        assert result is not None
        assert result["core_hit"] == 0
        assert result["cascade_rescued"] == 0
        assert result["miss"] == 5  # 全て miss 扱い (derived from total)
        assert result["total"] == 5
        assert result["core_only_hit_rate"] == "0/5"
        # per_proposition は空 mapping のまま（不明を装わない）
        assert result["per_proposition"] == {}
        # 内部整合
        assert result["core_hit"] + result["cascade_rescued"] + result["miss"] == 5

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

    def test_oversize_mapping_guards_against_negative_miss(self):
        """想定外入力 (hit_sources が total を超過) で miss が負にならないことを保証"""
        # total=2 なのに 3 件の tfidf hit がある異常入力
        result = summarize_hit_sources(
            {0: "tfidf", 1: "tfidf", 2: "tfidf"}, 2,
        )
        assert result is not None
        assert result["core_hit"] == 3  # explicit count はそのまま
        assert result["cascade_rescued"] == 0
        # max(0, ...) ガードで miss は 0 に clamp (負数にはならない)
        assert result["miss"] == 0
        # 内部整合は壊れる (3+0+0 > 2) が、これは upstream バグの顕在化。
        # 本関数は非負性だけ守る。
        assert result["total"] == 2
        assert result["core_hit"] >= 0
        assert result["miss"] >= 0


# --- パラメトリック不変条件: 全ケースで core + cascade + miss == total ---


class TestInternalConsistencyInvariant:
    """core + cascade + miss == total が (oversize 入力を除く) 全ケースで成立"""

    @pytest.mark.parametrize(
        "hit_sources,total",
        [
            ({}, 0),  # 命題なし → None 返却なのでこの test の対象外
            ({0: "tfidf"}, 1),
            ({0: "tfidf", 1: "tfidf", 2: "tfidf"}, 3),
            ({0: "miss", 1: "miss"}, 2),
            ({0: "cascade_rescued"}, 1),
            ({0: "tfidf", 1: "cascade_rescued", 2: "miss"}, 3),
            ({0: "tfidf"}, 5),  # incomplete mapping (4 unmapped)
            ({}, 3),  # compat fallback: 全て miss 扱い
            ({0: "tfidf", 2: "miss"}, 4),  # gap in keys + unmapped
        ],
        ids=[
            "empty_total_zero",
            "single_tfidf",
            "all_tfidf",
            "all_miss_explicit",
            "single_cascade",
            "mixed_full",
            "incomplete_mapping_1of5",
            "empty_mapping_with_total",
            "sparse_mapping_with_gap",
        ],
    )
    def test_sum_equals_total(self, hit_sources, total):
        """全ケースで core_hit + cascade_rescued + miss == total"""
        result = summarize_hit_sources(hit_sources, total)
        if total == 0:
            assert result is None  # 0 totals は None 返却
            return
        assert result is not None
        assert (
            result["core_hit"] + result["cascade_rescued"] + result["miss"]
            == result["total"]
        ), (
            f"internal consistency broken: "
            f"core={result['core_hit']} + cascade={result['cascade_rescued']} + "
            f"miss={result['miss']} != total={result['total']}"
        )
        # miss は常に非負
        assert result["miss"] >= 0
