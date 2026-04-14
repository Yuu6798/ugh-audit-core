from __future__ import annotations

from ugh_audit import dependencies
from ugh_audit import mcp_server
from ugh_audit import server
from ugh_audit.reference.golden_store import GoldenStore


def test_server_and_mcp_share_dependency_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("UGH_AUDIT_DB", str(tmp_path / "shared.db"))
    dependencies.reset()
    dependencies.configure(golden=GoldenStore(path=tmp_path / "golden_shared.json"))

    db_from_server = server._get_db()
    db_from_mcp = mcp_server._get_db()
    golden_from_server = server._get_golden()
    golden_from_mcp = mcp_server._get_golden()

    assert db_from_server is db_from_mcp
    assert golden_from_server is golden_from_mcp

    dependencies.reset()


def test_configure_none_none_resets_shared_dependencies(tmp_path, monkeypatch):
    monkeypatch.setenv("UGH_AUDIT_DB", str(tmp_path / "reset.db"))
    dependencies.reset()
    dependencies.configure(golden=GoldenStore(path=tmp_path / "golden_reset.json"))

    first_db = server._get_db()
    first_golden = server._get_golden()
    server.configure(db=None, golden=None)
    dependencies.configure(golden=GoldenStore(path=tmp_path / "golden_reset_2.json"))

    second_db = mcp_server._get_db()
    second_golden = mcp_server._get_golden()

    assert first_db is not second_db
    assert first_golden is not second_golden

    dependencies.reset()
