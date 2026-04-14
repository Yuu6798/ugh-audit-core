"""tests/test_grv_calculator.py — grv v1.4 受け入れテスト"""
from __future__ import annotations

import numpy as np
import pytest

from grv_calculator import (
    W_COLLAPSE_V2,
    W_DISPERSION,
    W_DRIFT,
    _split_sentences,
    compute_collapse_v2,
    compute_cover_soft,
    compute_dispersion,
    compute_drift,
    compute_grv,
    cos01,
)

try:
    import sentence_transformers  # noqa: F401
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False


class TestCos01:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert cos01(v, v) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        v = np.array([1.0, 0.0, 0.0])
        assert cos01(v, -v) == pytest.approx(0.0)

    def test_zero_vector(self):
        v = np.array([1.0, 2.0, 3.0])
        z = np.array([0.0, 0.0, 0.0])
        assert 0.0 <= cos01(v, z) <= 1.0

    def test_range_random(self):
        rng = np.random.default_rng(42)
        for _ in range(100):
            a = rng.standard_normal(384)
            b = rng.standard_normal(384)
            assert 0.0 <= cos01(a, b) <= 1.0


class TestOldFormulaRemoved:
    def test_no_entropy_ratio(self):
        import ugh_calculator
        src = open(ugh_calculator.__file__, encoding="utf-8").read()
        assert "entropy_ratio" not in src
        assert "centroid_cosine" not in src


class TestDrift:
    def test_identical_gives_zero(self):
        v = np.array([1.0, 2.0, 3.0])
        d, raw = compute_drift(v, v)
        assert d == pytest.approx(0.0)
        assert raw == pytest.approx(1.0)

    def test_opposite_gives_one(self):
        v = np.array([1.0, 0.0, 0.0])
        d, raw = compute_drift(v, -v)
        assert d == pytest.approx(1.0)
        assert raw == pytest.approx(-1.0)

    def test_range(self):
        rng = np.random.default_rng(42)
        for _ in range(50):
            a = rng.standard_normal(8)
            b = rng.standard_normal(8)
            d, _ = compute_drift(a, b)
            assert 0.0 <= d <= 1.0


class TestDispersion:
    def test_single_sentence(self):
        v = np.array([[1.0, 2.0, 3.0]])
        G = np.mean(v, axis=0)
        assert compute_dispersion(v, G) == 0.0

    def test_diverse_sentences(self):
        v = np.array([[1.0, 0.0], [-1.0, 0.0]])
        G = np.mean(v, axis=0)
        d = compute_dispersion(v, G)
        assert 0.0 < d <= 1.0


class TestCollapseV2:
    def test_empty_sentences(self):
        sent = np.array([]).reshape(0, 3)
        props = np.array([[1.0, 0.0, 0.0]])
        c, applicable, _ = compute_collapse_v2(sent, props)
        assert c == 0.0
        assert applicable is False

    def test_empty_propositions(self):
        sent = np.array([[1.0, 0.0, 0.0]])
        props = np.array([]).reshape(0, 3)
        c, applicable, _ = compute_collapse_v2(sent, props)
        assert c == 0.0
        assert applicable is False

    def test_perfect_match(self):
        """文と命題が同一なら collapse_v2 は低い"""
        sent = np.array([[1.0, 0.0], [0.0, 1.0]])
        props = np.array([[1.0, 0.0], [0.0, 1.0]])
        c, applicable, affs = compute_collapse_v2(sent, props)
        assert applicable is True
        assert c < 0.1  # cos01 同一ベクトル→1.0, 残留→0

    def test_no_match(self):
        """文と命題が直交すれば collapse_v2 は高い"""
        sent = np.array([[1.0, 0.0, 0.0]])
        props = np.array([[0.0, 1.0, 0.0]])
        c, applicable, _ = compute_collapse_v2(sent, props)
        assert applicable is True
        assert c > 0.3  # cos01(直交) = 0.5, 残留 = 0.5


class TestCoverSoft:
    def test_empty(self):
        sent = np.array([[1.0, 0.0]])
        props = np.array([]).reshape(0, 2)
        cs, _ = compute_cover_soft(sent, props)
        assert cs == 0.0

    def test_perfect_cover(self):
        sent = np.array([[1.0, 0.0], [0.0, 1.0]])
        props = np.array([[1.0, 0.0], [0.0, 1.0]])
        cs, per_prop = compute_cover_soft(sent, props)
        assert cs == pytest.approx(1.0)
        assert len(per_prop) == 2


class TestSplitSentences:
    def test_japanese(self):
        assert len(_split_sentences("文1。文2。文3。")) == 3

    def test_english(self):
        assert len(_split_sentences("Sentence one. Sentence two.")) == 2

    def test_empty(self):
        assert _split_sentences("") == []


class TestWeights:
    def test_active_weights_positive(self):
        assert W_DRIFT > 0
        assert W_DISPERSION > 0

    def test_collapse_v2_positive(self):
        assert W_COLLAPSE_V2 > 0


@pytest.mark.skipif(not _HAS_SBERT, reason="sentence-transformers not installed")
class TestComputeGrv:
    def test_grv_nonzero(self):
        result = compute_grv(
            question="AIは意味を理解できるのか？",
            response_text="AIは統計的パターンを認識しますが、人間のような意味理解はできません。"
                          "しかし、文脈に応じた応答は可能です。",
            question_meta={
                "core_propositions": [
                    "AIの意味理解は統計的パターン認識に基づく",
                    "人間の意味理解とは質的に異なる",
                    "文脈処理能力は意味理解と同義ではない",
                ],
            },
            metadata_source="inline",
        )
        assert result is not None
        assert result.grv != 0.0

    def test_grv_range(self):
        result = compute_grv(
            question="テスト質問",
            response_text="テスト回答です。これはテストです。",
        )
        assert result is not None
        assert 0.0 <= result.grv <= 1.0

    def test_components_present(self):
        result = compute_grv(
            question="テスト質問",
            response_text="テスト回答です。これはテストです。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="inline",
        )
        assert result is not None
        assert 0.0 <= result.drift <= 1.0
        assert 0.0 <= result.dispersion <= 1.0
        assert 0.0 <= result.collapse_v2 <= 1.0
        assert 0.0 <= result.cover_soft <= 1.0
        assert 0.0 <= result.wash_index <= 1.0

    def test_single_sentence_dispersion_zero(self):
        result = compute_grv(question="テスト", response_text="1文だけの回答")
        assert result is not None
        assert result.dispersion == 0.0

    def test_no_propositions(self):
        result = compute_grv(question="テスト", response_text="テスト回答です。")
        assert result is not None
        assert result.collapse_v2 == 0.0
        assert result.collapse_v2_applicable is False
        assert result.cover_soft == 0.0

    def test_missing_meta(self):
        result = compute_grv(
            question="テスト", response_text="テスト回答です。", metadata_source="none",
        )
        assert result is not None
        assert result.ref_confidence == 0.50
        assert result.meta_source == "missing"

    def test_auto_meta(self):
        result = compute_grv(
            question="テスト", response_text="テスト回答です。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="llm_generated",
        )
        assert result is not None
        assert result.ref_confidence == 0.70

    def test_output_schema_v14(self):
        result = compute_grv(
            question="テスト", response_text="テスト回答。もう一文。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="inline",
        )
        assert result is not None
        assert hasattr(result, "collapse_v2")
        assert hasattr(result, "cover_soft")
        assert hasattr(result, "wash_index")
        assert hasattr(result, "wash_index_c")
        assert hasattr(result, "prop_affinity_per_sentence")
        assert hasattr(result, "cover_soft_per_proposition")
        assert result.weights["w_c"] > 0

    def test_empty_response_fallback(self):
        result = compute_grv(question="テスト", response_text="")
        assert result is not None
        assert 0.0 <= result.grv <= 1.0
