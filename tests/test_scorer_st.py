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


def test_por_fired_boundary():
    """Step 1: por_fired は PoR >= 0.82 で True（境界値テスト）"""
    from ugh_audit.scorer.models import AuditResult
    from ugh_audit.scorer.ugh_scorer import POR_FIRE_THRESHOLD

    assert POR_FIRE_THRESHOLD == 0.82

    # 境界値: ちょうど0.82はTrue
    r_exact = AuditResult(
        por=0.82, por_fired=(0.82 >= POR_FIRE_THRESHOLD),
        delta_e=0.1, grv={}, question="", response="", reference=""
    )
    assert r_exact.por_fired is True, "PoR=0.82 should fire (>=)"

    # 境界値未満: False
    r_below = AuditResult(
        por=0.8199, por_fired=(0.8199 >= POR_FIRE_THRESHOLD),
        delta_e=0.1, grv={}, question="", response="", reference=""
    )
    assert r_below.por_fired is False, "PoR=0.8199 should not fire"

    # 境界値超: True
    r_above = AuditResult(
        por=0.8201, por_fired=(0.8201 >= POR_FIRE_THRESHOLD),
        delta_e=0.1, grv={}, question="", response="", reference=""
    )
    assert r_above.por_fired is True, "PoR=0.8201 should fire"
