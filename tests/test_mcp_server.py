"""
tests/test_mcp_server.py
MCP サーバーのテスト — audit_answer ツールの動作を検証
"""
from __future__ import annotations

import asyncio
import json

import pytest

from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.scorer.ugh_scorer import UGHScorer
from ugh_audit.storage.audit_db import AuditDB

# MCP SDK はオプショナル依存 — なければスキップ
mcp_mod = pytest.importorskip("mcp")

from ugh_audit.mcp_server import configure, mcp  # noqa: E402


@pytest.fixture
def tmp_db(tmp_path):
    return AuditDB(db_path=tmp_path / "test_mcp.db")


@pytest.fixture
def tmp_golden(tmp_path):
    return GoldenStore(path=tmp_path / "golden.json")


@pytest.fixture(autouse=True)
def _configure_mcp(tmp_db, tmp_golden):
    """MCP サーバーのグローバルインスタンスをテスト用に差し替える"""
    scorer = UGHScorer(model_id="test-mcp")
    configure(db=tmp_db, scorer=scorer, golden=tmp_golden)
    yield
    configure(db=None, scorer=None, golden=None)


def _run(coro):
    """asyncio.run のヘルパー"""
    return asyncio.get_event_loop().run_until_complete(coro)


def test_tools_list_includes_audit_answer():
    """tools/list に audit_answer が含まれることを検証"""
    tools = _run(mcp.list_tools())
    tool_names = [t.name for t in tools]
    assert "audit_answer" in tool_names


def test_audit_answer_tool_schema():
    """audit_answer のスキーマが正しいことを検証"""
    tools = _run(mcp.list_tools())
    audit_tool = next(t for t in tools if t.name == "audit_answer")
    schema = audit_tool.inputSchema
    assert "question" in schema["properties"]
    assert "response" in schema["properties"]
    assert "reference" in schema["properties"]
    # question と response は必須
    assert "question" in schema["required"]
    assert "response" in schema["required"]


def test_audit_answer_returns_structured_payload():
    """tools/call で audit_answer を呼び、期待するペイロードが返ることを検証"""
    content, _ = _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
        "reference": "AIは意味と共振する動的プロセスです。",
    }))
    assert len(content) >= 1
    data = json.loads(content[0].text)
    assert "por" in data
    assert "delta_e" in data
    assert "grv" in data
    assert "verdict" in data
    assert "saved_id" in data
    assert isinstance(data["saved_id"], int)
    assert data["saved_id"] >= 1


def test_audit_answer_saves_to_db(tmp_db):
    """audit_answer の結果が DB に保存されることを検証"""
    _run(mcp.call_tool("audit_answer", {
        "question": "テスト質問",
        "response": "テスト回答",
    }))
    rows = tmp_db.list_recent(10)
    assert len(rows) == 1
    assert rows[0]["question"] == "テスト質問"


def test_audit_answer_without_reference():
    """reference 省略時に正常動作することを検証"""
    content, _ = _run(mcp.call_tool("audit_answer", {
        "question": "テスト",
        "response": "テスト回答",
    }))
    data = json.loads(content[0].text)
    assert "verdict" in data
    assert data["saved_id"] >= 1


def test_audit_answer_resolves_golden_reference(tmp_db, tmp_golden):
    """reference 省略時に GoldenStore から自動解決されることを検証"""
    ref = tmp_golden.find_reference("AIは意味を持てるか？")
    assert ref is not None

    _run(mcp.call_tool("audit_answer", {
        "question": "AIは意味を持てるか？",
        "response": "AIは意味を処理できます。",
    }))

    rows = tmp_db.list_recent(1)
    assert rows[0]["reference"] == ref


def test_streamable_http_app_created():
    """MCP Streamable HTTP アプリが生成可能であることを検証"""
    app = mcp.streamable_http_app()
    # Starlette アプリが返される
    assert callable(app)
