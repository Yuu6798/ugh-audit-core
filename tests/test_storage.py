"""
tests/test_storage.py
AuditDB の基本テスト（パイプライン A 対応）
"""
import pytest
from ugh_audit.storage.audit_db import AuditDB


@pytest.fixture
def tmp_db(tmp_path):
    return AuditDB(db_path=tmp_path / "test_audit.db")


def test_save_and_list(tmp_db):
    row_id = tmp_db.save(
        question="テスト質問",
        response="テスト回答",
        S=0.95,
        C=0.80,
        delta_e=0.05,
        quality_score=4.80,
        verdict="accept",
        f1=0.0,
        f2=0.0,
        f3=0.0,
        f4=0.0,
        session_id="test-session",
    )
    assert row_id == 1

    recent = tmp_db.list_recent(10)
    assert len(recent) == 1
    assert recent[0]["S"] == pytest.approx(0.95)
    assert recent[0]["C"] == pytest.approx(0.80)
    assert recent[0]["delta_e"] == pytest.approx(0.05)
    assert recent[0]["quality_score"] == pytest.approx(4.80)
    assert recent[0]["verdict"] == "accept"


def test_session_summary(tmp_db):
    for i in range(3):
        tmp_db.save(
            delta_e=0.05 * (i + 1),
            quality_score=5.0 - 4.0 * 0.05 * (i + 1),
            session_id="session-A",
        )
    tmp_db.save(session_id="session-B")

    summary = tmp_db.session_summary("session-A")
    assert summary["total"] == 3
    assert summary["avg_delta_e"] == pytest.approx(0.10)


def test_drift_history(tmp_db):
    for i in range(5):
        tmp_db.save(
            S=0.95,
            C=0.80,
            delta_e=0.05,
            quality_score=4.80,
            verdict="accept",
        )
    history = tmp_db.drift_history(limit=3)
    assert len(history) == 3
