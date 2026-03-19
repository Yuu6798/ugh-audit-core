"""
tests/test_storage.py
AuditDB の基本テスト
"""
import pytest
from ugh_audit.scorer.models import AuditResult
from ugh_audit.storage.audit_db import AuditDB


@pytest.fixture
def tmp_db(tmp_path):
    return AuditDB(db_path=tmp_path / "test_audit.db")


def make_result(**kwargs) -> AuditResult:
    defaults = dict(
        question="テスト質問",
        response="テスト回答",
        por=0.85,
        por_fired=True,
        delta_e=0.03,
        grv={"意味": 0.4},
        model_id="test-model",
        session_id="test-session",
    )
    defaults.update(kwargs)
    return AuditResult(**defaults)


def test_save_and_list(tmp_db):
    r = make_result()
    row_id = tmp_db.save(r)
    assert row_id == 1

    recent = tmp_db.list_recent(10)
    assert len(recent) == 1
    assert recent[0]["por"] == pytest.approx(0.85)
    assert recent[0]["por_fired"] == 1


def test_session_summary(tmp_db):
    for i in range(3):
        tmp_db.save(make_result(
            delta_e=0.05 * (i + 1),
            session_id="session-A"
        ))
    tmp_db.save(make_result(session_id="session-B"))

    summary = tmp_db.session_summary("session-A")
    assert summary["total"] == 3
    assert summary["fired_count"] == 3
    assert summary["avg_delta_e"] == pytest.approx(0.10)


def test_drift_history(tmp_db):
    for i in range(5):
        tmp_db.save(make_result())
    history = tmp_db.drift_history(limit=3)
    assert len(history) == 3
