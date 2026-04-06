"""
tests/test_collector.py
AuditCollector の基本テスト（パイプライン A 対応）
"""
import pytest
from ugh_audit.collector.audit_collector import AuditCollector
from ugh_audit.storage.audit_db import AuditDB
from ugh_audit.reference.golden_store import GoldenStore


@pytest.fixture
def tmp_collector(tmp_path):
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    return AuditCollector(db=db, golden=golden)


def test_collect_returns_dict(tmp_collector):
    result = tmp_collector.collect(
        question="AIは意味を持てるか？",
        response="AIは意味位相空間で共振する動的プロセスです。",
    )
    assert isinstance(result, dict)
    assert "S" in result
    assert "C" in result
    assert "delta_e" in result
    assert "quality_score" in result
    assert "verdict" in result
    assert result["verdict"] in ("accept", "rewrite", "regenerate", "degraded")


def test_collect_degraded_not_saved_to_db(tmp_collector):
    """degraded 結果は DB に保存されない"""
    result = tmp_collector.collect(
        question="PoRとは何か？",
        response="意味の発火点です。",
        session_id="test-session",
    )
    assert result["verdict"] == "degraded"
    assert result["saved_id"] is None
    recent = tmp_collector._db.list_recent(10)
    assert len(recent) == 0


def test_collect_batch_degraded(tmp_collector):
    """collector は question_meta なしで常に degraded → DB 保存なし"""
    pairs = [
        {"question": f"質問{i}", "response": f"回答{i}"}
        for i in range(3)
    ]
    results = tmp_collector.collect_batch(pairs, session_id="batch-session")
    assert len(results) == 3
    assert all(r["verdict"] == "degraded" for r in results)
    assert all(r["saved_id"] is None for r in results)
    summary = tmp_collector._db.session_summary("batch-session")
    assert summary["total"] == 0


def test_collect_degraded_returns_none_values(tmp_collector):
    """question_meta なし → degraded: C/delta_e/quality_score は None, DB 保存なし"""
    result = tmp_collector.collect(
        question="テスト質問",
        response="テスト回答",
    )
    assert result["C"] is None
    assert result["delta_e"] is None
    assert result["quality_score"] is None
    assert result["verdict"] == "degraded"
    assert result["hit_rate"] is None
    assert result["saved_id"] is None

    # DB に保存されていないことを確認
    rows = tmp_collector._db.list_recent(1)
    assert len(rows) == 0


def test_session_context_manager_degraded(tmp_collector):
    """session コンテキスト内でも degraded は DB 保存されない"""
    with tmp_collector.session("ctx-session") as s:
        s.collect(question="Q1", response="R1")
        s.collect(question="Q2", response="R2")

    assert len(s.results) == 2
    assert all(r["verdict"] == "degraded" for r in s.results)
    summary = s.summary()
    assert summary["total"] == 0
