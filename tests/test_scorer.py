"""
tests/test_scorer.py
UGHScorer の基本テスト
"""
from ugh_audit.scorer.models import AuditResult
from ugh_audit.scorer.ugh_scorer import UGHScorer


def test_audit_result_meaning_drift():
    r = AuditResult(question="Q", response="R", delta_e=0.02)
    assert r.meaning_drift == "同一意味圏"

    r2 = AuditResult(question="Q", response="R", delta_e=0.07)
    assert r2.meaning_drift == "軽微なズレ"

    r3 = AuditResult(question="Q", response="R", delta_e=0.15)
    assert r3.meaning_drift == "意味乖離"


def test_audit_result_dominant_gravity():
    r = AuditResult(
        question="Q", response="R",
        grv={"意味": 0.41, "処理": 0.28, "AI": 0.15}
    )
    assert r.dominant_gravity == "意味"

    r_empty = AuditResult(question="Q", response="R", grv={})
    assert r_empty.dominant_gravity is None


def test_scorer_minimal():
    """依存ライブラリなしでも動作すること"""
    scorer = UGHScorer(model_id="test")
    result = scorer.score(
        question="AIは意味を持てるか？",
        response="AIは意味を処理できます。",
        session_id="test-001",
    )
    assert isinstance(result, AuditResult)
    assert result.model_id == "test"
    assert result.session_id == "test-001"
    assert 0.0 <= result.por <= 1.0
    assert 0.0 <= result.delta_e <= 1.0


def test_scorer_repr():
    r = AuditResult(
        question="Q", response="R",
        por=0.85, por_fired=True, delta_e=0.03
    )
    assert "🔥" in repr(r)
    assert "同一意味圏" in repr(r)
