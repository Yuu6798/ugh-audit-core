"""tests/test_hit_sources.py — summarize_hit_sources 単体テスト

Paper defense: core pipeline (tfidf) vs cascade layer (cascade_rescued) の
分離を API 出力 JSON に surfacing するヘルパーの契約を固定する。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from ugh_calculator import reconstruct_hit_sources, summarize_hit_sources  # noqa: E402


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


# --- reconstruct_hit_sources: evidence バージョン差分の吸収 ---


class _LegacyEvidenceMinimal:
    """propositions_hit / propositions_total のみ持つ legacy 相当 mock。

    hit_sources / hit_ids 属性なし。pre-cascade era の Evidence を模擬する。
    """

    def __init__(self, hits: int, total: int):
        self.propositions_hit = hits
        self.propositions_total = total


class _LegacyEvidenceWithHitIds:
    """hit_ids / miss_ids は持つが hit_sources 属性が欠落している中間版。"""

    def __init__(self, hit_ids, miss_ids, total):
        self.hit_ids = hit_ids
        self.miss_ids = miss_ids
        self.propositions_hit = len(hit_ids)
        self.propositions_total = total


class _ModernEvidence:
    """現行 Evidence を模擬: hit_sources を直接持つ"""

    def __init__(self, hit_sources, total):
        self.hit_sources = hit_sources
        self.hit_ids = [i for i, v in hit_sources.items() if v != "miss"]
        self.miss_ids = [i for i, v in hit_sources.items() if v == "miss"]
        self.propositions_hit = len(self.hit_ids)
        self.propositions_total = total


class TestReconstructHitSources:
    """evidence のバージョン差分を吸収するフォールバック"""

    def test_modern_evidence_uses_hit_sources_directly(self):
        ev = _ModernEvidence({0: "tfidf", 1: "cascade_rescued", 2: "miss"}, 3)
        result = reconstruct_hit_sources(ev)
        assert result == {0: "tfidf", 1: "cascade_rescued", 2: "miss"}

    def test_intermediate_evidence_rebuilds_from_hit_ids(self):
        """hit_sources 属性なし、hit_ids/miss_ids あり → tfidf/miss に attribute"""
        ev = _LegacyEvidenceWithHitIds(hit_ids=[0, 2], miss_ids=[1], total=3)
        # hit_sources 属性を欠落させる
        assert not hasattr(ev, "hit_sources")
        result = reconstruct_hit_sources(ev)
        assert result == {0: "tfidf", 2: "tfidf", 1: "miss"}

    def test_legacy_evidence_with_only_aggregate_counts(self):
        """propositions_hit だけ持つ legacy → hits を core に attribute"""
        ev = _LegacyEvidenceMinimal(hits=2, total=3)
        result = reconstruct_hit_sources(ev)
        # 順序情報なしのため 0..1 が hit, 2 が miss
        assert result == {0: "tfidf", 1: "tfidf", 2: "miss"}

    def test_legacy_evidence_with_all_hits(self):
        """全命題 hit の legacy ケース"""
        ev = _LegacyEvidenceMinimal(hits=3, total=3)
        result = reconstruct_hit_sources(ev)
        assert result == {0: "tfidf", 1: "tfidf", 2: "tfidf"}

    def test_legacy_evidence_with_zero_hits(self):
        """hits=0 の legacy: 全て miss に attribute"""
        ev = _LegacyEvidenceMinimal(hits=0, total=3)
        result = reconstruct_hit_sources(ev)
        # hits==0 なので最小 legacy path は発火しない (空 dict 返却)
        # 残りは total=3 からの reconstruction に任せる挙動
        # 現実装では hits>0 条件があるため、empty mapping を返す
        assert result == {}

    def test_no_usable_fields_returns_empty(self):
        """何の hit 情報もない object → 空 mapping"""
        class Empty:
            pass
        result = reconstruct_hit_sources(Empty())
        assert result == {}

    def test_empty_hit_sources_falls_through_to_hit_ids(self):
        """hit_sources={} が truthy 判定で false のため hit_ids にフォールバックする"""
        class Mixed:
            hit_sources = {}  # 空
            hit_ids = [0]
            miss_ids = [1]
            propositions_hit = 1
            propositions_total = 2
        result = reconstruct_hit_sources(Mixed())
        # 空 hit_sources は skip され、hit_ids/miss_ids に fallback
        assert result == {0: "tfidf", 1: "miss"}

    def test_legacy_reconstruction_is_consistent_with_summarize(self):
        """legacy reconstruct + summarize で hit_rate と矛盾しないことを確認

        Codex review P2 の本丸: 以前は server.py が {} を渡し summarize が
        core=0, miss=total を返して propositions_hit と矛盾していた。
        reconstruct を介せば hit_rate と summary の core_hit が一致する。
        """
        ev = _LegacyEvidenceMinimal(hits=5, total=10)
        reconstructed = reconstruct_hit_sources(ev)
        summary = summarize_hit_sources(reconstructed, ev.propositions_total)
        assert summary is not None
        # core_hit = propositions_hit と一致 (hit_rate = 5/10 に整合)
        assert summary["core_hit"] == ev.propositions_hit
        assert summary["miss"] == ev.propositions_total - ev.propositions_hit
        assert summary["total"] == ev.propositions_total
        assert summary["core_only_hit_rate"] == f"{ev.propositions_hit}/{ev.propositions_total}"
