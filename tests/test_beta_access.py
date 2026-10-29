"""Offline adversarial coverage of the invite boundary and both HTTP transports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from threading import Barrier, Event
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from starlette.testclient import TestClient  # noqa: E402

from ugh_audit import beta_access  # noqa: E402
from ugh_audit.reference.golden_store import GoldenStore  # noqa: E402
from ugh_audit.server import app, configure  # noqa: E402
from ugh_audit.storage.audit_db import AuditDB  # noqa: E402


TOKENS = {"alice": "test_alice_" + "a" * 40, "bob": "test_bob_" + "b" * 40}


def _credential(owner, token=None, **extra):
    return {"user_id": owner,
            "token_sha256": hashlib.sha256((token or TOKENS[owner]).encode()).hexdigest(),
            **extra}


def _headers(owner="alice"):
    return {"Authorization": "Bearer " + TOKENS[owner],
            "X-UGH-Anthropic-Key": "offline-provider-key-" + owner}


def _payload(owner="alice", **extra):
    return {"question": "Describe " + owner, "response": owner + " is present.",
            "session_id": "shared-session", "reference": "Explicit reference.",
            "question_meta": {"question": "Describe " + owner,
                              "core_propositions": [owner + " is present."]}, **extra}


@pytest.fixture
def invite(tmp_path, monkeypatch, semantic_complete_judge):
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [_credential("alice"), _credential("bob")]}))
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    usage_path = tmp_path / "usage.sqlite"
    monkeypatch.setenv("UGH_USAGE_DB", str(usage_path))
    monkeypatch.setenv("UGH_REQUESTS_PER_MINUTE", "1000")
    for variable in ("UGH_AUDITS_PER_DAY", "UGH_GLOBAL_AUDITS_PER_DAY",
                     "UGH_CONCURRENT_PER_USER", "UGH_CONCURRENT_GLOBAL", "UGH_REMOTE_API"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: semantic_complete_judge)
    monkeypatch.setattr("ugh_audit.mcp_server._make_semantic_judge", lambda: semantic_complete_judge)
    db = AuditDB(tmp_path / "audits.sqlite")
    configure(db=db, golden=GoldenStore(tmp_path / "golden.json"))
    try:
        with TestClient(app, headers={"X-UGH-Anthropic-Key": "offline-test-provider-key"}, base_url="http://localhost") as client:
            yield SimpleNamespace(client=client, db=db, registry=registry, usage_path=usage_path)
    finally:
        configure()


def _rpc(client, method, params=None, owner="alice", **kwargs):
    headers = {**_headers(owner), "Accept": "application/json, text/event-stream"}
    response = client.post("/mcp/", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, **kwargs)
    assert response.status_code == 200, response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    lines = [line.removeprefix("data: ") for line in response.text.splitlines()
             if line.startswith("data: ")]
    assert len(lines) == 1, response.text
    return json.loads(lines[0])


def _tool(client, name, arguments=None, owner="alice"):
    envelope = _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, owner)
    assert "error" not in envelope, envelope
    result = envelope["result"]
    if result.get("isError"):
        return result
    if "structuredContent" in result:
        structured = result["structuredContent"]
        return structured["result"] if set(structured) == {"result"} else structured
    texts = [item["text"] for item in result["content"] if item["type"] == "text"]
    assert len(texts) == 1, result
    return json.loads(texts[0])


def _attempts(invite):
    with sqlite3.connect(invite.usage_path) as connection:
        return connection.execute("SELECT COALESCE(SUM(attempts), 0) FROM beta_daily_audits").fetchone()[0]


@pytest.mark.parametrize("path", ["/api/history", "/api/audit/1", "/api/session/shared-session",
                                  "/api/drift", "/openapi.json", "/docs", "/mcp/"])
def test_unauthenticated_surface_is_private(invite, path):
    response = invite.client.get(path)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert "no-store" in response.headers["Cache-Control"]
    assert invite.client.get("/health").status_code == 200


def test_unconfigured_default_is_closed(invite, monkeypatch):
    monkeypatch.delenv("UGH_ACCESS_MODE")
    monkeypatch.delenv("UGH_INVITE_FILE")
    assert invite.client.get("/api/history", headers=_headers()).status_code == 503
    assert invite.client.get("/health").status_code == 200


@pytest.mark.parametrize("authorization", ["Basic abc", "Bearer short", "Bearer " + "x" * 40,
                                           "Bearer  " + TOKENS["alice"]])
def test_malformed_or_unknown_bearer_is_rejected(invite, authorization):
    response = invite.client.get("/api/history", headers={"Authorization": authorization})
    assert response.status_code == 401


def test_duplicate_authentication_headers_are_rejected(invite):
    response = invite.client.get("/api/history", headers=[
        ("Authorization", "Bearer " + TOKENS["alice"]),
        ("Authorization", "Bearer " + TOKENS["bob"])])
    assert response.status_code == 401


@pytest.mark.parametrize("entry", [_credential("alice", enabled=False),
                                   _credential("alice", expires_at=1)])
def test_registry_revocation_and_expiry_take_effect_next_request(invite, entry):
    assert invite.client.get("/api/history", headers=_headers()).status_code == 200
    invite.registry.write_text(json.dumps({"users": [entry, _credential("bob")]}))
    assert invite.client.get("/api/history", headers=_headers()).status_code == 401
    assert invite.client.get("/api/history", headers=_headers("bob")).status_code == 200


@pytest.mark.parametrize("config", ["{", '{"users":[],"users":[]}',
                                    '{"users":[{"user_id":42}]}', '{"users":[]}',
                                    json.dumps({"users": [_credential("alice")], "label": "extra"}),
                                    json.dumps({"users": [_credential("alice", label="extra")]})])
def test_invalid_registry_never_opens_access(invite, config):
    invite.registry.write_text(config)
    response = invite.client.get("/api/history", headers=_headers())
    assert response.status_code == 503
    assert str(invite.registry) not in response.text
    assert TOKENS["alice"] not in response.text


def test_rest_history_summary_drift_retry_and_legacy_are_owner_scoped(invite):
    legacy_id = invite.db.save(question="legacy-private", response="legacy", session_id="shared-session")
    alice = invite.client.post("/api/audit", headers=_headers(), json=_payload()).json()
    bob = invite.client.post("/api/audit", headers=_headers("bob"), json=_payload("bob")).json()
    assert alice["saved_id"] and bob["saved_id"]
    for owner, own, other in (("alice", alice, bob), ("bob", bob, alice)):
        headers = _headers(owner)
        history = invite.client.get("/api/history", headers=headers).json()
        assert [row["id"] for row in history] == [own["saved_id"]]
        assert invite.client.get(f'/api/audit/{other["saved_id"]}', headers=headers).status_code == 404
        assert invite.client.get(f"/api/audit/{legacy_id}", headers=headers).status_code == 404
        summary = invite.client.get("/api/session/shared-session", headers=headers).json()
        assert summary["total"] == 1
        drift = invite.client.get("/api/drift", headers=headers).json()
        assert [row["response"] for row in drift] == [owner + " is present."]
        count = _attempts(invite)
        for retry_of in (other["saved_id"], legacy_id, 999999):
            denied = invite.client.post("/api/audit", headers=headers,
                                        json=_payload(owner, retry_of=retry_of))
            assert denied.status_code == 404
        assert _attempts(invite) == count
    retry = invite.client.post("/api/audit", headers=_headers(),
                               json=_payload(retry_of=alice["saved_id"]))
    assert retry.status_code == 200
    assert retry.json()["retry_of"] == alice["saved_id"]


def test_real_mcp_transport_authenticates_and_isolates_history(invite):
    initialized = _rpc(invite.client, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "offline-beta-regression", "version": "1"}})
    assert "result" in initialized
    alice = _tool(invite.client, "audit_answer", _payload())
    assert alice["saved_id"], alice
    assert _tool(invite.client, "get_history", owner="bob") == []
    assert _tool(invite.client, "get_audit", {"audit_id": alice["saved_id"]}, "bob")["error"]
    assert _tool(invite.client, "get_session_summary", {"session_id": "shared-session"}, "bob")["total"] == 0
    assert _tool(invite.client, "get_drift_history", owner="bob") == []
    own = _tool(invite.client, "get_history")
    assert [row["id"] for row in own] == [alice["saved_id"]]
    count = _attempts(invite)
    denied = _tool(invite.client, "audit_answer", _payload("bob", retry_of=alice["saved_id"]), "bob")
    assert denied["isError"] is True
    assert _attempts(invite) == count


def test_daily_quota_is_shared_across_rest_mcp_and_token_rotation(invite, monkeypatch):
    monkeypatch.setenv("UGH_AUDITS_PER_DAY", "1")
    assert invite.client.post("/api/audit", headers=_headers(), json=_payload()).status_code == 200
    rotated = "rotated_alice_" + "c" * 40
    invite.registry.write_text(json.dumps({"users": [_credential("alice", token=rotated)]}))
    assert invite.client.get("/api/history", headers=_headers()).status_code == 401
    response = invite.client.post("/api/audit", headers={"Authorization": "Bearer " + rotated},
                                  json=_payload())
    assert response.status_code == 429
    assert response.json()["detail"] == "daily_audit_limit"
    assert int(response.headers["Retry-After"]) > 0
    invite.registry.write_text(json.dumps({"users": [_credential("alice")]}))
    denied = _tool(invite.client, "audit_answer", _payload())
    assert denied["isError"] is True
    assert "daily_audit_limit" in str(denied)
    assert _attempts(invite) == 1


def test_quota_and_input_errors_precede_costly_pipeline(invite, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Expensive pipeline must not run")

    monkeypatch.setattr("ugh_audit.server._run_pipeline", forbidden)
    monkeypatch.setattr("ugh_audit.mcp_server._pipeline.run_audit", forbidden)
    assert invite.client.post("/api/audit", headers=_headers(),
                              json=_payload(response="x" * 32001)).status_code == 422
    oversized = _tool(invite.client, "audit_answer", _payload(response="x" * 32001))
    assert oversized["isError"] is True
    assert _attempts(invite) == 0
    with beta_access.usage_limiter().acquire_audit("alice"):
        response = invite.client.post("/api/audit", headers=_headers(), json=_payload())
        assert response.status_code == 429
        denied = _tool(invite.client, "audit_answer", _payload())
        assert denied["isError"] is True
    assert _attempts(invite) == 1


def test_authenticated_request_quota_applies_across_transports(invite, monkeypatch):
    monkeypatch.setenv("UGH_REQUESTS_PER_MINUTE", "1")
    assert invite.client.get("/api/history", headers=_headers()).status_code == 200
    response = invite.client.post("/mcp/", headers=_headers(), json={})
    assert response.status_code == 429
    assert response.json()["detail"] == "request_rate_exceeded"
    assert invite.client.get("/api/history", headers=_headers("bob")).status_code == 200
    assert invite.client.get("/health").status_code == 200


def test_global_daily_quota_blocks_other_owner_before_execution(invite, monkeypatch):
    monkeypatch.setenv("UGH_GLOBAL_AUDITS_PER_DAY", "1")
    response = invite.client.post("/api/audit", headers=_headers(),
                                  json=_payload(question_meta=None))
    assert response.status_code == 200
    assert response.json()["mode"] == "degraded"
    # A degraded admitted attempt consumes the same quota as a saved audit.
    denied = _tool(invite.client, "audit_answer", _payload("bob"), "bob")
    assert denied["isError"] is True
    assert "global_daily_audit_limit" in str(denied)
    assert _attempts(invite) == 1


@pytest.mark.parametrize("name,value", [("UGH_AUDITS_PER_DAY", "0"),
                                        ("UGH_CONCURRENT_GLOBAL", "bad")])
def test_invalid_usage_config_returns_503(invite, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    response = invite.client.post("/api/audit", headers=_headers(), json=_payload())
    assert response.status_code == 503


def test_database_failure_does_not_run_pipeline(invite, monkeypatch, tmp_path):
    monkeypatch.setenv("UGH_USAGE_DB", str(tmp_path))
    response = invite.client.post("/api/audit", headers=_headers(), json=_payload())
    assert response.status_code == 503


def test_oversized_body_is_rejected_before_parsing(invite):
    response = invite.client.post("/api/audit", headers=_headers(), content="x" * 262145)
    assert response.status_code == 413


def test_concurrent_rest_and_mcp_contexts_keep_different_owners(invite, monkeypatch,
                                                              semantic_complete_judge):
    barrier = Barrier(2)

    def judge(requirement, answer, *, timeout_seconds):
        barrier.wait(timeout=10)
        return semantic_complete_judge(requirement, answer, timeout_seconds=timeout_seconds)

    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: judge)
    monkeypatch.setattr("ugh_audit.mcp_server._make_semantic_judge", lambda: judge)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rest = pool.submit(invite.client.post, "/api/audit", headers=_headers(), json=_payload())
        mcp = pool.submit(_tool, invite.client, "audit_answer", _payload("bob"), "bob")
        assert rest.result(timeout=15).status_code == 200
        assert mcp.result(timeout=15)["saved_id"]
    for owner in ("alice", "bob"):
        rows = invite.client.get("/api/history", headers=_headers(owner)).json()
        assert [row["response"] for row in rows] == [owner + " is present."]


@pytest.mark.parametrize("transport", ["rest", "mcp"])
def test_client_cancellation_retains_slot_until_real_worker_finishes(
        invite, monkeypatch, semantic_complete_judge, transport):
    import anyio
    import httpx

    started, finish = Event(), Event()

    def judge(requirement, answer, *, timeout_seconds):
        started.set()
        assert finish.wait(10), "Test must release the actual worker"
        return semantic_complete_judge(requirement, answer, timeout_seconds=timeout_seconds)

    monkeypatch.setattr("ugh_audit.server._make_semantic_judge", lambda: judge)
    monkeypatch.setattr("ugh_audit.mcp_server._make_semantic_judge", lambda: judge)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                     base_url="http://localhost") as client:
            async def request(task_status=anyio.TASK_STATUS_IGNORED):
                with anyio.CancelScope() as scope:
                    task_status.started(scope)
                    if transport == "rest":
                        await client.post("/api/audit", headers=_headers(), json=_payload())
                    else:
                        await client.post("/mcp/", headers={**_headers(), "Accept":
                                          "application/json, text/event-stream"}, json={
                            "jsonrpc": "2.0", "id": 123, "method": "tools/call",
                            "params": {"name": "audit_answer", "arguments": _payload()}})

            async with anyio.create_task_group() as group:
                scope = await group.start(request)
                try:
                    assert await anyio.to_thread.run_sync(started.wait, 5)
                    scope.cancel()
                    await anyio.sleep(0)
                    denied = await client.post("/api/audit", headers=_headers(), json=_payload())
                    assert denied.status_code == 429
                    assert denied.json()["detail"] == "concurrent_audit_limit"
                    assert _attempts(invite) == 1
                    with sqlite3.connect(invite.usage_path) as connection:
                        assert connection.execute("SELECT COUNT(*) FROM beta_audit_leases").fetchone()[0] == 1
                finally:
                    finish.set()

    anyio.run(scenario)
    # MCP workers belong to the server lifespan and may outlive the cancelled HTTP task.
    deadline = time.monotonic() + 5
    while True:
        with sqlite3.connect(invite.usage_path) as connection:
            remaining = connection.execute("SELECT COUNT(*) FROM beta_audit_leases").fetchone()[0]
        if remaining == 0:
            break
        assert time.monotonic() < deadline, "Completed worker did not release its lease"
        Event().wait(0.01)
    assert len(invite.db.list_recent(owner_id="alice")) == 1


def test_failed_permit_validation_releases_reservation(invite, monkeypatch):
    from ugh_audit.usage_limits import UsageUnavailable

    limiter = beta_access.usage_limiter()

    def failed_renew(lease_id):
        raise UsageUnavailable()

    monkeypatch.setattr(limiter, "_renew", failed_renew)
    response = invite.client.post("/api/audit", headers=_headers(), json=_payload())
    assert response.status_code == 503
    with sqlite3.connect(invite.usage_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM beta_audit_leases").fetchone()[0] == 0
    assert _attempts(invite) == 1
