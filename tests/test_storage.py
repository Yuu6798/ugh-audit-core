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
    assert recent[0]["S"] is None
    assert recent[0]["C"] is None
    assert recent[0]["delta_e"] is None
    assert recent[0]["quality_score"] is None
    assert recent[0]["verdict"] == "degraded"
    assert recent[0]["requires_review"] is True
    legacy = recent[0]["legacy_metrics"]
    assert legacy["S"] == pytest.approx(0.95)
    assert legacy["C"] == pytest.approx(0.80)
    assert legacy["delta_e"] == pytest.approx(0.05)
    assert legacy["quality_score"] == pytest.approx(4.80)
    assert legacy["verdict"] == "accept"
    with tmp_db._conn() as conn:
        assert conn.execute("SELECT S,C,delta_e,quality_score,verdict FROM audit_runs WHERE id=?", (row_id,)).fetchone() == (
            0.95, 0.80, 0.05, 4.80, "accept",
        )


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
    assert summary["verified_total"] == 0
    assert summary["unverified_total"] == 3
    assert summary["avg_delta_e"] is None
    assert summary["legacy_summary"]["avg_delta_e"] == pytest.approx(0.10)


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
    # delta_e=0.30 のレコードも追加（backfill 検証用）
    conn.execute("""
        INSERT INTO audit_runs
            (session_id, model_id, question, response, por, por_fired,
             delta_e, meaning_drift, created_at)
        VALUES ('s1', 'test', 'Q2', 'R2', 0.50, 0, 0.30, '意味乖離', '2026-01-02T00:00:00')
    """)
    # delta_e=0.0 のレコード（zero-ΔE backfill 検証用）
    conn.execute("""
        INSERT INTO audit_runs
            (session_id, model_id, question, response, por, por_fired,
             delta_e, meaning_drift, created_at)
        VALUES ('s1', 'test', 'Q3', 'R3', 0.99, 1, 0.0, '同一意味圏', '2026-01-03T00:00:00')
    """)
    conn.commit()
    conn.close()

    # AuditDB を開くとマイグレーション + backfill が実行される
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
    assert len(rows) == 4

    # レガシー行の quality_score / verdict が backfill されていること
    legacy_rows = sorted(
        [r for r in rows if r["session_id"] == "s1"],
        key=lambda r: r["created_at"],
    )
    # delta_e=0.03 → quality_score=4.88, verdict=accept
    assert legacy_rows[0]["legacy_metrics"]["quality_score"] == pytest.approx(4.88, abs=0.01)
    assert legacy_rows[0]["legacy_metrics"]["verdict"] == "accept"
    # delta_e=0.30 → quality_score=3.80, verdict=regenerate
    assert legacy_rows[1]["legacy_metrics"]["quality_score"] == pytest.approx(3.80, abs=0.01)
    assert legacy_rows[1]["legacy_metrics"]["verdict"] == "regenerate"
    # delta_e=0.0 → quality_score=5.0 (default), verdict=accept
    assert legacy_rows[2]["legacy_metrics"]["quality_score"] == pytest.approx(5.0)
    assert legacy_rows[2]["legacy_metrics"]["verdict"] == "accept"
    assert all(row["verdict"] == "degraded" and row["requires_review"] for row in legacy_rows)
    assert all(row["quality_score"] is None for row in legacy_rows)
