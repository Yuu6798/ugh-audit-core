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


def test_migration_from_v02_schema(tmp_path):
    """v0.2 レガシースキーマの既存DBに対してマイグレーションが正常に動作することを検証"""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    # v0.2 スキーマを手動で作成（旧 AuditResult ベース）
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE audit_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            model_id    TEXT NOT NULL DEFAULT 'unknown',
            question    TEXT NOT NULL,
            response    TEXT NOT NULL,
            reference   TEXT,
            por         REAL NOT NULL,
            por_fired   INTEGER NOT NULL,
            delta_e     REAL NOT NULL,
            grv_json    TEXT NOT NULL DEFAULT '{}',
            meaning_drift TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        INSERT INTO audit_runs
            (session_id, model_id, question, response, por, por_fired,
             delta_e, meaning_drift, created_at)
        VALUES ('s1', 'test', 'Q', 'R', 0.85, 1, 0.03, '同一意味圏', '2026-01-01T00:00:00')
    """)
    conn.commit()
    conn.close()

    # AuditDB を開くとマイグレーションが実行される
    db = AuditDB(db_path=db_path)

    # 新カラムで INSERT できること
    row_id = db.save(
        session_id="s2",
        question="新Q",
        response="新R",
        S=0.95,
        C=0.80,
        delta_e=0.05,
        quality_score=4.80,
        verdict="accept",
    )
    assert row_id >= 1

    # 新旧両方のデータが取得できること
    rows = db.list_recent(10)
    assert len(rows) == 2
