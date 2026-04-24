"""
tests/test_collector.py
AuditCollector / SessionCollector — deprecation warning contract test

AuditCollector は v0.4 で非推奨化、v0.5 で削除予定。本テストは
「import / 初期化時に DeprecationWarning が発生する」という API 契約を固定する。
旧 snapshot test (degraded 固定の挙動 lock) は廃止した — 非推奨 API に
対する snapshot は設計意図ではなく現状追認だったため。
"""
import warnings

import pytest

from ugh_audit.collector.audit_collector import AuditCollector
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB


def test_audit_collector_emits_deprecation_warning(tmp_path):
    """AuditCollector() 初期化時に DeprecationWarning が出ること"""
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    with pytest.warns(DeprecationWarning, match="AuditCollector"):
        AuditCollector(db=db, golden=golden)


def test_session_collector_emits_deprecation_warning(tmp_path):
    """SessionCollector 初期化 (session() コンテキスト入口) でも警告が出ること"""
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        collector = AuditCollector(db=db, golden=golden)
    with pytest.warns(DeprecationWarning, match="AuditCollector"):
        with collector.session("test-session"):
            pass


def test_deprecation_message_points_to_migration_path(tmp_path):
    """警告メッセージに REST / MCP への移行手順が含まれること"""
    db = AuditDB(db_path=tmp_path / "test.db")
    golden = GoldenStore(path=tmp_path / "golden.json")
    with pytest.warns(DeprecationWarning) as record:
        AuditCollector(db=db, golden=golden)
    message = str(record[0].message)
    assert "/audit" in message or "audit_answer" in message
    assert "server_api.md" in message
    assert "v0.5" in message
