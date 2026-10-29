"""Request-only provider credentials across actual REST/MCP and proxy boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from email.message import Message
import hashlib
import io
import json
import os
import sqlite3
from threading import Barrier
from types import SimpleNamespace
import urllib.error
import urllib.request
import urllib.response

import pytest
from starlette.testclient import TestClient

from ugh_audit import beta_access, mcp_server, semantic_matching, server
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB


HOST_KEY = "host_key_must_never_pay_" + "h" * 40
KEYS = {"alice": "caller_alice_key_" + "a" * 40, "bob": "caller_bob_key_" + "b" * 40}
TOKENS = {owner: "invite_" + owner + "_" + owner[0] * 40 for owner in KEYS}


def _headers(owner="alice", *, provider=False):
    result = {"Authorization": "Bearer " + TOKENS[owner]}
    if provider:
        result[beta_access.PROVIDER_KEY_HEADER] = KEYS[owner]
    return result


def _payload(owner="alice", **changes):
    return {"question": "Describe " + owner, "response": owner + " is present.",
            "reference": "Explicit reference", "session_id": "shared",
            "question_meta": {"core_propositions": [owner + " is present."]}, **changes}


@pytest.fixture
def byok(tmp_path, monkeypatch):
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [
        {"user_id": owner, "token_sha256": hashlib.sha256(token.encode()).hexdigest()}
        for owner, token in TOKENS.items()]}))
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    monkeypatch.setenv("UGH_REQUESTS_PER_MINUTE", "1000")
    monkeypatch.setenv("UGH_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setenv("UGH_META_CACHE_DIR", str(tmp_path / "meta-cache"))
    monkeypatch.delenv("UGH_REMOTE_API", raising=False)
    monkeypatch.delenv("UGH_REMOTE_TOKEN", raising=False)
    calls = []

    class OfflineProvider:
        def __init__(self, key, timeout):
            self.key, self.messages = key, self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            content = json.loads(kwargs["messages"][0]["content"])
            calls.append((self.key, content["answer"]))
            value = {"status": "fulfilled", "missing_kind": None,
                     "evidence": [content["answer"]], "reason": "Offline grounded test control"}
            return SimpleNamespace(model=semantic_matching.MODEL, stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(value))])

    monkeypatch.setattr(semantic_matching, "AnthropicHTTP", OfflineProvider)
    db = AuditDB(tmp_path / "audit.db")
    server.configure(db=db, golden=GoldenStore(tmp_path / "golden.json"))
    try:
        with TestClient(server.app, base_url="http://localhost") as client:
            yield SimpleNamespace(client=client, db=db, calls=calls, provider=OfflineProvider,
                                  directory=tmp_path)
    finally:
        server.configure()


def _rpc(client, method, params=None, *, owner="alice", provider=False):
    response = client.post("/mcp/", headers={**_headers(owner, provider=provider),
                           "Accept": "application/json, text/event-stream"}, json={
        "jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200, response.text
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    values = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert len(values) == 1
    return values[0]


def _audit(byok, transport, *, owner="alice", provider=False, **changes):
    if transport == "rest":
        response = byok.client.post("/api/audit", headers=_headers(owner, provider=provider),
                                    json=_payload(owner, **changes))
        return response.status_code, response.json()
    result = _rpc(byok.client, "tools/call", {"name": "audit_answer",
                  "arguments": _payload(owner, **changes)}, owner=owner, provider=provider)["result"]
    return 200, result if result.get("isError") else result["structuredContent"]


@pytest.mark.parametrize("mode", ["invite", "local"])
@pytest.mark.parametrize("transport", ["rest", "mcp"])
@pytest.mark.parametrize("auto_generate", [False, True])
def test_http_never_uses_host_key_when_caller_key_is_missing(
        byok, monkeypatch, mode, transport, auto_generate):
    monkeypatch.setenv("UGH_ACCESS_MODE", mode)
    status, result = _audit(byok, transport, auto_generate_meta=auto_generate,
                            question_meta=None if auto_generate else _payload()["question_meta"])
    assert "provider_key_required" in str(result)
    assert status == (400 if transport == "rest" else 200)
    assert byok.calls == []
    assert byok.db.list_recent() == []
    assert not byok.directory.joinpath("meta-cache").exists()
    if mode == "invite":
        with sqlite3.connect(byok.directory / "usage.db") as conn:
            assert conn.execute("SELECT COUNT(*) FROM beta_daily_audits").fetchone() == (0,)


@pytest.mark.parametrize("invalid", [b"", b"short", b"a" * 513, b"a" * 20 + b" ",
                                     b"a" * 20 + b"\t", b"a" * 20 + b"\n", b"a" * 20 + b"\x7f",
                                     b"a" * 20 + b"\xff"])
def test_invalid_provider_header_is_rejected_without_echo_or_provider_call(byok, invalid):
    response = byok.client.post("/api/audit", headers=[
        (b"authorization", ("Bearer " + TOKENS["alice"]).encode()),
        (b"x-ugh-anthropic-key", invalid)], json=_payload())
    assert response.status_code == 400
    assert response.json() == {"detail": "invalid_provider_key"}
    assert byok.calls == []


def test_duplicate_provider_header_is_not_merged_or_accepted(byok):
    response = byok.client.post("/api/audit", headers=[
        ("Authorization", "Bearer " + TOKENS["alice"]),
        ("X-UGH-Anthropic-Key", KEYS["alice"]),
        ("x-ugh-anthropic-key", KEYS["bob"])], json=_payload())
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_provider_key"
    assert byok.calls == []


def test_two_http_owners_keep_keys_separate_and_never_persist_them(byok, monkeypatch, caplog):
    barrier = Barrier(2)
    create = byok.provider.create

    def create_together(self, **kwargs):
        barrier.wait(timeout=10)
        return create(self, **kwargs)

    monkeypatch.setattr(byok.provider, "create", create_together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(_audit, byok, "rest", owner="alice", provider=True)
        second = pool.submit(_audit, byok, "mcp", owner="bob", provider=True)
        results = [first.result(timeout=20), second.result(timeout=20)]
    assert all(status == 200 and result["saved_id"] for status, result in results)
    assert set(byok.calls) == {(KEYS[owner], owner + " is present.") for owner in KEYS}
    assert os.environ["ANTHROPIC_API_KEY"] == HOST_KEY
    assert not beta_access.is_http_request()
    for owner in KEYS:
        history = byok.client.get("/api/history", headers=_headers(owner))
        assert history.status_code == 200
        assert [row["response"] for row in history.json()] == [owner + " is present."]
        printable = repr(beta_access.Principal(owner, TOKENS[owner], KEYS[owner]))
        for secret in (*KEYS.values(), HOST_KEY):
            assert secret not in history.text + str(results) + caplog.text + printable
            for database in (byok.directory / "audit.db", byok.directory / "usage.db"):
                assert secret.encode() not in database.read_bytes()


@pytest.mark.parametrize("transport", ["rest", "mcp"])
def test_provider_errors_do_not_fall_back_or_echo_keys(byok, monkeypatch, caplog, transport):
    def failure(self, **kwargs):
        byok.calls.append(self.key)
        raise ValueError("private error " + self.key + " " + HOST_KEY)

    monkeypatch.setattr(byok.provider, "create", failure)
    status, result = _audit(byok, transport, provider=True)
    assert status == 200 and result["mode"] == "degraded"
    assert byok.calls == [KEYS["alice"]]
    assert byok.db.list_recent() == []
    for key in (KEYS["alice"], HOST_KEY):
        assert key not in str(result) + caplog.text


def test_capabilities_discovery_reads_and_cors_do_not_require_provider_key(byok):
    assert byok.client.get("/api/capabilities").status_code == 401
    response = byok.client.get("/api/capabilities", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"provider_credentials": "request_only", "byok_version": 1}
    assert byok.client.get("/api/history", headers=_headers()).status_code == 200
    assert "result" in _rpc(byok.client, "initialize", {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "offline-byok-test", "version": "1"}})
    tools = _rpc(byok.client, "tools/list")["result"]["tools"]
    for tool in tools:
        assert not any("key" in name.lower() or "credential" in name.lower()
                       for name in tool["inputSchema"].get("properties", {}))
    schema = byok.client.get("/openapi.json", headers=_headers()).json()
    assert not any("key" in name.lower() or "credential" in name.lower()
                   for name in schema["components"]["schemas"]["AuditRequest"]["properties"])
    preflight = byok.client.options("/api/audit", headers={"Origin": "https://chatgpt.com",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,x-ugh-anthropic-key,content-type"})
    assert preflight.status_code == 200
    assert "x-ugh-anthropic-key" in preflight.headers["Access-Control-Allow-Headers"].lower()
    assert byok.calls == []


def test_context_free_python_retains_explicit_environment_only_outside_http(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    assert not beta_access.is_http_request()
    assert beta_access.request_provider_key() == HOST_KEY
    marker = beta_access._principal.set(beta_access.Principal("alice", TOKENS["alice"]))
    try:
        assert beta_access.is_http_request()
        assert beta_access.request_provider_key() is None
        assert server._make_semantic_judge() is None
        assert mcp_server._make_semantic_judge() is None
    finally:
        beta_access._principal.reset(marker)


@contextmanager
def _principal(key=KEYS["alice"]):
    marker = beta_access._principal.set(beta_access.Principal("alice", TOKENS["alice"], key))
    try:
        yield
    finally:
        beta_access._principal.reset(marker)


def _remote_response(value, url, status=200, location=None):
    headers = Message()
    if location:
        headers["Location"] = location
    response = urllib.response.addinfourl(io.BytesIO(json.dumps(value).encode()), headers, url, status)
    response.msg = "OK" if status == 200 else "Found"
    return response


def test_proxy_checks_authenticated_capability_before_forwarding_only_caller_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    requests = []

    def open_request(request, timeout):
        requests.append((request.get_method(), request.full_url, dict(request.header_items())))
        value = beta_access.BYOK_CAPABILITIES if request.full_url.endswith("capabilities") else []
        return _remote_response(value, request.full_url)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    with _principal():
        mcp_server._proxy_audit("https://upstream.invalid", **_payload())
        mcp_server._proxy_get("https://upstream.invalid", "/api/history")
    assert [method for method, _, _ in requests] == ["GET", "POST", "GET"]
    assert requests[0][1] == "https://upstream.invalid/api/capabilities"
    for method, _, raw_headers in requests:
        headers = {name.lower(): value for name, value in raw_headers.items()}
        assert headers["authorization"] == "Bearer " + TOKENS["alice"]
        assert headers.get("x-ugh-anthropic-key") == (KEYS["alice"] if method == "POST" else None)
        assert HOST_KEY not in str(headers)


@pytest.mark.parametrize("capability", [{}, {"byok_version": 1, "provider_credentials": "host"},
    {"byok_version": True, "provider_credentials": "request_only"},
    {"byok_version": 2, "provider_credentials": "request_only"},
    {**beta_access.BYOK_CAPABILITIES, "extra": "unsupported"}])
def test_proxy_never_posts_to_legacy_or_unsupported_upstream(monkeypatch, capability):
    requests = []

    def open_request(request, timeout):
        requests.append(request)
        return _remote_response(capability, request.full_url)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_request))
    with _principal(), pytest.raises(beta_access.AccessError, match="remote_byok_unsupported"):
        mcp_server._proxy_audit("https://upstream.invalid", **_payload())
    assert len(requests) == 1 and requests[0].get_method() == "GET"
    assert all(name.lower() != "x-ugh-anthropic-key" for name, _ in requests[0].header_items())


def test_proxy_missing_request_key_cannot_fall_back_to_host(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)

    def forbidden(*args, **kwargs):
        raise AssertionError("No upstream request is permitted without a caller key")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    with _principal(None), pytest.raises(beta_access.AccessError, match="provider_key_required"):
        mcp_server._proxy_audit("https://upstream.invalid", **_payload())


@pytest.mark.parametrize("stage", ["capability", "audit"])
def test_proxy_does_not_redirect_credentials_or_post_after_capability_redirect(monkeypatch, stage):
    requests = []
    builder = urllib.request.build_opener

    class FakeTLS(urllib.request.HTTPSHandler):
        def https_open(self, request):
            requests.append(request)
            if stage == "audit" and request.get_method() == "GET":
                return _remote_response(beta_access.BYOK_CAPABILITIES, request.full_url)
            return _remote_response({}, request.full_url, status=302,
                                    location="https://untrusted.invalid/steal")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: builder(FakeTLS(), *handlers))
    with _principal():
        if stage == "capability":
            with pytest.raises(beta_access.AccessError, match="remote_byok_unavailable"):
                mcp_server._proxy_audit("https://upstream.invalid", **_payload())
        else:
            assert mcp_server._proxy_audit("https://upstream.invalid", **_payload()).mode == "degraded"
    assert len(requests) == (1 if stage == "capability" else 2)
    assert all(request.full_url.startswith("https://upstream.invalid/") for request in requests)


def test_local_proxy_still_requires_explicit_provider_and_upstream_invite(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    monkeypatch.setenv("UGH_ACCESS_MODE", "local")
    monkeypatch.delenv("UGH_REMOTE_TOKEN", raising=False)
    with pytest.raises(beta_access.AccessError, match="remote_byok_auth_required"):
        mcp_server._proxy_audit("https://upstream.invalid", **_payload())
