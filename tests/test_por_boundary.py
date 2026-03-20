"""
tests/test_por_boundary.py
PoR = 0.82 ちょうどで fired = True になることを確認
"""
from ugh_audit.scorer.models import AuditResult
from ugh_audit.scorer.ugh_scorer import POR_FIRE_THRESHOLD


def test_por_fired_at_exact_threshold():
    """PoR = 0.82 ちょうどで fired = True になることを確認"""
    assert POR_FIRE_THRESHOLD == 0.82

    r = AuditResult(
        question="Q", response="R",
        por=0.82, por_fired=(0.82 >= POR_FIRE_THRESHOLD),
    )
    assert r.por_fired is True, "PoR=0.82 should fire (>=)"


def test_por_not_fired_below_threshold():
    """PoR < 0.82 で fired = False"""
    r = AuditResult(
        question="Q", response="R",
        por=0.8199, por_fired=(0.8199 >= POR_FIRE_THRESHOLD),
    )
    assert r.por_fired is False


def test_por_fired_above_threshold():
    """PoR > 0.82 で fired = True"""
    r = AuditResult(
        question="Q", response="R",
        por=0.83, por_fired=(0.83 >= POR_FIRE_THRESHOLD),
    )
    assert r.por_fired is True
