"""
tests/test_scorer_st.py
sentence-transformers バックエンドのテスト
sentence-transformers がインストールされている場合のみ実行
"""
import pytest

st = pytest.importorskip("sentence_transformers", reason="sentence-transformers not installed")


def test_st_backend_returns_nonzero():
    from ugh_audit.scorer import UGHScorer

    s = UGHScorer()
    assert s.backend == "sentence-transformers", f"Expected sentence-transformers, got {s.backend}"

    result = s.score(
        question="AIは意味を持てるか？",
        response="AIは意味を処理できますが主観的体験は持ちません。",
        reference="AIは意味を持つのではなく意味位相空間で共振する動的プロセスです。",
    )
    assert result.por > 0.0, f"PoR should be non-zero, got {result.por}"
    assert result.delta_e >= 0.0, f"ΔE should be non-negative, got {result.delta_e}"
    assert isinstance(result.grv, dict)


def test_st_backend_por_range():
    from ugh_audit.scorer import UGHScorer

    s = UGHScorer()
    result = s.score(question="テスト", response="テスト回答", reference="参照回答")
    assert 0.0 <= result.por <= 1.0
    assert 0.0 <= result.delta_e <= 1.0
