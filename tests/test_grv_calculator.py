"""tests/test_grv_calculator.py — grv v1.2 受け入れテスト (AC-1〜AC-15)"""
from __future__ import annotations


import numpy as np
import pytest

from grv_calculator import (
    W_COLLAPSE,
    W_DISPERSION,
    W_DRIFT,
    _split_sentences,
    compute_collapse,
    compute_dispersion,
    compute_drift,
    compute_grv,
    cos01,
)

# SBert 未インストール環境ではスキップ
try:
    import sentence_transformers  # noqa: F401
    _HAS_SBERT = True
except ImportError:
    _HAS_SBERT = False


# --- AC-15: cos01 が常に [0, 1] ---

class TestCos01:
    def test_identical_vectors(self):
        v = np.array([1.0, 2.0, 3.0])
        assert cos01(v, v) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        v = np.array([1.0, 0.0, 0.0])
        assert cos01(v, -v) == pytest.approx(0.0)

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        assert cos01(a, b) == pytest.approx(0.5)

    def test_zero_vector(self):
        v = np.array([1.0, 2.0, 3.0])
        z = np.array([0.0, 0.0, 0.0])
        result = cos01(v, z)
        assert 0.0 <= result <= 1.0

    def test_range_random(self):
        rng = np.random.default_rng(42)
        for _ in range(100):
            a = rng.standard_normal(384)
            b = rng.standard_normal(384)
            assert 0.0 <= cos01(a, b) <= 1.0


# --- AC-1: 旧2項式が削除されている ---

class TestOldFormulaRemoved:
    def test_no_entropy_ratio_in_calculator(self):
        import ugh_calculator
        src = open(ugh_calculator.__file__, encoding="utf-8").read()
        assert "entropy_ratio" not in src
        assert "centroid_cosine" not in src


# --- 成分テスト ---

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


# --- AC-5: n_sent=1 で dispersion=0.0 ---

class TestDispersion:
    def test_single_sentence(self):
        v = np.array([[1.0, 2.0, 3.0]])
        G = np.mean(v, axis=0)
        assert compute_dispersion(v, G) == 0.0

    def test_identical_sentences(self):
        v = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        G = np.mean(v, axis=0)
        assert compute_dispersion(v, G) == pytest.approx(0.0)

    def test_diverse_sentences(self):
        v = np.array([[1.0, 0.0], [-1.0, 0.0]])
        G = np.mean(v, axis=0)
        d = compute_dispersion(v, G)
        assert 0.0 < d <= 1.0


# --- AC-6: n_props<2 で collapse=0.0, collapse_applicable=False ---

class TestCollapse:
    def test_single_proposition(self):
        sent = np.array([[1.0, 0.0, 0.0]])
        props = np.array([[1.0, 0.0, 0.0]])
        c, applicable, _, _ = compute_collapse(sent, props, [1.0])
        assert c == 0.0
        assert applicable is False

    def test_zero_propositions(self):
        sent = np.array([[1.0, 0.0, 0.0]])
        props = np.array([]).reshape(0, 3)
        c, applicable, _, _ = compute_collapse(sent, props, [])
        assert c == 0.0
        assert applicable is False

    def test_uniform_distribution(self):
        """全命題に均等にヒットすれば collapse は低い"""
        sent = np.array([[1.0, 0.0], [0.0, 1.0]])
        props = np.array([[1.0, 0.0], [0.0, 1.0]])
        c, applicable, _, _ = compute_collapse(sent, props, [1.0, 1.0])
        assert applicable is True

    def test_concentrated_distribution(self):
        """1命題にのみ集中すれば collapse は均等分布より高い"""
        sent_even = np.array([[1.0, 0.0], [0.0, 1.0]])
        props = np.array([[1.0, 0.0], [0.0, 1.0]])
        c_even, _, _, _ = compute_collapse(sent_even, props, [1.0, 1.0])
        sent_conc = np.array([[1.0, 0.0], [0.99, 0.01]])
        c_conc, applicable, _, _ = compute_collapse(sent_conc, props, [1.0, 1.0])
        assert applicable is True
        assert c_conc > c_even


# --- フォールバックテスト ---

class TestSplitSentences:
    def test_japanese(self):
        text = "これは文1。これは文2。これは文3。"
        assert len(_split_sentences(text)) == 3

    def test_empty(self):
        assert _split_sentences("") == []

    def test_no_punctuation(self):
        """句読点なしは1文"""
        text = "句読点がない文"
        assert len(_split_sentences(text)) == 1


# --- AC-14: 文分割失敗時にフォールバック ---

class TestWeights:
    def test_active_weights_positive(self):
        """合成に使う重み (drift + dispersion) が正"""
        assert W_DRIFT > 0
        assert W_DISPERSION > 0

    def test_collapse_excluded(self):
        """collapse は合成値から除外 (w_c=0)"""
        assert W_COLLAPSE == 0.0


# --- SBert 依存テスト ---

@pytest.mark.skipif(not _HAS_SBERT, reason="sentence-transformers not installed")
class TestComputeGrv:
    """AC-2〜AC-12: SBert 実動テスト"""

    def test_grv_nonzero(self):
        """AC-2: grv が 0 以外の値を出力する"""
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
        """AC-3: grv の値域が [0, 1]"""
        result = compute_grv(
            question="テスト質問",
            response_text="テスト回答です。これはテストです。",
        )
        assert result is not None
        assert 0.0 <= result.grv <= 1.0

    def test_components_present(self):
        """AC-4: 3成分が個別に出力される"""
        result = compute_grv(
            question="テスト質問",
            response_text="テスト回答です。これはテストです。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="inline",
        )
        assert result is not None
        assert 0.0 <= result.drift <= 1.0
        assert 0.0 <= result.dispersion <= 1.0
        assert 0.0 <= result.collapse <= 1.0

    def test_single_sentence_dispersion_zero(self):
        """AC-5: 1文回答で dispersion=0.0"""
        result = compute_grv(
            question="テスト",
            response_text="1文だけの回答",
        )
        assert result is not None
        assert result.dispersion == 0.0
        assert result.n_sentences == 1

    def test_no_propositions_collapse_zero(self):
        """AC-6: 命題なしで collapse=0.0, collapse_applicable=False"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。これはテストです。",
        )
        assert result is not None
        assert result.collapse == 0.0
        assert result.collapse_applicable is False

    def test_single_proposition_collapse_zero(self):
        """AC-6: 命題1で collapse=0.0, collapse_applicable=False"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。これはテストです。",
            question_meta={"core_propositions": ["命題A"]},
            metadata_source="inline",
        )
        assert result is not None
        assert result.collapse == 0.0
        assert result.collapse_applicable is False

    def test_missing_meta_fallback(self):
        """AC-7: question_meta 欠落時の ref_confidence=0.50, meta_scale=0.00"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。",
            metadata_source="none",
        )
        assert result is not None
        assert result.ref_confidence == 0.50
        assert result.meta_scale == 0.0
        assert result.meta_source == "missing"

    def test_auto_meta_confidence(self):
        """AC-8: auto meta で ref_confidence=0.70, meta_scale=0.40"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="llm_generated",
        )
        assert result is not None
        assert result.ref_confidence == 0.70
        assert result.meta_scale == pytest.approx(0.40)
        assert result.meta_source == "auto"

    def test_manual_prop_weight(self):
        """AC-9: manual_core 命題の prop_weight=1.0"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。もう一文。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="inline",
        )
        assert result is not None
        assert all(w == 1.0 for w in result.prop_weights)

    def test_auto_prop_weight(self):
        """AC-10: meta_derived 命題の prop_weight=meta_scale"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。もう一文。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="llm_generated",
        )
        assert result is not None
        assert all(w == pytest.approx(0.40) for w in result.prop_weights)

    def test_output_schema(self):
        """AC-11: grv, grv_components, grv_meta が含まれる"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。",
            question_meta={"core_propositions": ["命題A", "命題B"]},
            metadata_source="inline",
        )
        assert result is not None
        assert isinstance(result.grv, float)
        assert isinstance(result.drift, float)
        assert isinstance(result.dispersion, float)
        assert isinstance(result.collapse, float)
        assert isinstance(result.n_sentences, int)
        assert isinstance(result.n_propositions, int)
        assert isinstance(result.meta_source, str)

    def test_meta_scale_in_result(self):
        """AC-12: grv_meta に meta_scale が含まれる"""
        result = compute_grv(
            question="テスト",
            response_text="テスト回答です。",
        )
        assert result is not None
        assert hasattr(result, "meta_scale")
        assert isinstance(result.meta_scale, float)

    def test_empty_response_fallback(self):
        """AC-14: 空文字列入力でフォールバック"""
        result = compute_grv(
            question="テスト",
            response_text="",
        )
        assert result is not None
        assert 0.0 <= result.grv <= 1.0

    def test_special_chars_fallback(self):
        """AC-14: 特殊文字入力でフォールバック"""
        result = compute_grv(
            question="???!!!",
            response_text="@#$%^&*()",
        )
        assert result is not None
        assert 0.0 <= result.grv <= 1.0
