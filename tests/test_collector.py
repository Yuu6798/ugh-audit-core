"""
tests/test_collector.py
AuditCollector の基本テスト
"""
import pytest
from ugh_audit.collector.audit_collector import AuditCollector
from ugh_audit.scorer.models import AuditResult
from ugh_audit.storage.audit_db import AuditDB
from ugh_audit.reference.golden_store import GoldenStore


@pytest.fixture
def tmp_collector(tmp_path):
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    return AuditCollector(model_id="test", db=db, golden=golden)


def test_collect_returns_audit_result(tmp_collector):
    result = tmp_collector.collect(
        question="AIは意味を持てるか？",
        response="AIは意味位相空間で共振する動的プロセスです。",
    )
    assert isinstance(result, AuditResult)
    assert result.model_id == "test"
    assert 0.0 <= result.por <= 1.0
    assert 0.0 <= result.delta_e <= 1.0


def test_collect_saves_to_db(tmp_collector):
    tmp_collector.collect(
        question="PoRとは何か？",
        response="意味の発火点です。",
        session_id="test-session",
    )
    recent = tmp_collector._db.list_recent(10)
    assert len(recent) == 1
    assert recent[0]["session_id"] == "test-session"


def test_collect_batch(tmp_collector):
    pairs = [
        {"question": f"質問{i}", "response": f"回答{i}"}
        for i in range(3)
    ]
    results = tmp_collector.collect_batch(pairs, session_id="batch-session")
    assert len(results) == 3
    summary = tmp_collector._db.session_summary("batch-session")
    assert summary["total"] == 3


def test_session_context_manager(tmp_collector):
    with tmp_collector.session("ctx-session") as s:
        s.collect(question="Q1", response="R1")
        s.collect(question="Q2", response="R2")

    assert len(s.results) == 2
    summary = s.summary()
    assert summary["total"] == 2


def test_backend_property(tmp_collector):
    assert tmp_collector.backend in ("ugh3-metrics-lib", "sentence-transformers", "minimal")
