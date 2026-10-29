"""The proxy preserves caller identity, TLS boundaries and exact history coverage."""
from __future__ import annotations

from contextlib import contextmanager
from email.message import Message
import io
import hashlib
import json
from types import SimpleNamespace
import urllib.error
import urllib.request
import urllib.response

import pytest
from starlette.testclient import TestClient

from ugh_audit import beta_access, mcp_server, server
from ugh_audit.semantic_matching import MODEL, apply_semantic_gate, assess_requirements
from ugh_audit.storage.audit_db import AuditDB


TOKEN = "test_alice_" + "a" * 40


@contextmanager
def principal(owner="alice", token=TOKEN):
    marker = beta_access._principal.set(beta_access.Principal(owner, token))
    try:
        yield
    finally:
        beta_access._principal.reset(marker)


def response(data, *, status=200, url="https://upstream.invalid/api/history", location=None):
    headers = Message()
    if location:
        headers["Location"] = location
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    result = urllib.response.addinfourl(io.BytesIO(raw), headers, url, status)
    result.msg = "OK" if status == 200 else "Found"
    return result


def test_proxy_forwards_request_identity_instead_of_shared_environment_key(monkeypatch):
    requests = []
    monkeypatch.setenv("UGH_REMOTE_TOKEN", "shared-administrator-credential")

    def open_request(req, timeout):
        requests.append(req)
        return response([])

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers:
                        SimpleNamespace(open=open_request))
    with principal():
        assert mcp_server._proxy_get("https://upstream.invalid", "/api/history") == []
    assert requests[0].get_header("Authorization") == "Bearer " + TOKEN


@pytest.mark.parametrize("url", ["http://upstream.invalid", "ftp://upstream.invalid",
                                 "https://user:pass@upstream.invalid", "https:///no-host"])
def test_authenticated_proxy_rejects_insecure_or_credential_bearing_urls(monkeypatch, url):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unsafe destination must be rejected before network I/O")

    monkeypatch.setattr(urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    with principal(), pytest.raises(ValueError, match="requires HTTPS"):
        mcp_server._remote_open(urllib.request.Request(url), timeout=1)


def test_redirect_is_not_followed_and_never_sends_token_to_second_host(monkeypatch):
    visited = []
    original_builder = urllib.request.build_opener

    class FakeTLS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            visited.append(req.full_url)
            return response(b"", status=302, url=req.full_url,
                            location="https://second-host.invalid/collect")

    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *handlers: original_builder(FakeTLS(), *handlers))
    with principal(), pytest.raises(urllib.error.HTTPError) as error:
        mcp_server._remote_open(urllib.request.Request("https://upstream.invalid/api/history"), 1)
    assert error.value.code == 302
    assert visited == ["https://upstream.invalid/api/history"]


def test_proxy_requires_context_even_if_shared_upstream_key_is_configured(monkeypatch):
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_REMOTE_TOKEN", "shared-administrator-credential")
    with pytest.raises(beta_access.AccessError, match="authentication_required"):
        mcp_server._remote_open(urllib.request.Request("https://upstream.invalid"), 1)


def test_proxy_failure_does_not_return_upstream_exception_text(monkeypatch):
    def failed(*args, **kwargs):
        raise ValueError("private response: " + TOKEN)

    monkeypatch.setattr(mcp_server, "_remote_open", failed)
    result = mcp_server._proxy_get("https://upstream.invalid", "/api/history")
    assert result == {"error": "remote_api_error:ValueError"}
    assert TOKEN not in str(result)


def fraction_row():
    meta = {"question": "Delivery instructions", "core_propositions": ["first", "second", "third"]}
    answer = "first second"

    def judge(requirement, answer, *, timeout_seconds):
        missing = requirement == "third"
        item = {"status": "missing" if missing else "fulfilled",
                "missing_kind": "absence" if missing else None,
                "evidence": [] if missing else [answer], "reason": "test proof"}
        return {**item, "api_called": True, "requested_model": MODEL, "returned_model": MODEL,
                "stop_reason": "end_turn", "unknown_source": None,
                "raw_response": json.dumps(item)}

    assessment = assess_requirements(meta, answer, judge)
    return apply_semantic_gate({
        "id": 1, "question": meta["question"], "response": answer,
        "question_meta": meta, "schema_version": "4.0.0", "metadata_source": "inline",
        "created_at": "2026-10-29T00:00:00Z",
    }, assessment, response=answer, question_meta=meta)


@pytest.mark.parametrize("path", ["/api/history?limit=20", "/api/audit/1", "/api/drift?limit=100"])
def test_rest_history_and_drift_roundtrip_preserve_fractional_coverage(monkeypatch, path):
    row = fraction_row()
    assert row["C"] == 2 / 3
    if path.startswith("/api/drift"):
        from ugh_audit.storage.audit_db import guard_history_row
        row = guard_history_row(row)
        monkeypatch.setattr(server, "_get_db", lambda: SimpleNamespace(
            drift_history=lambda **kwargs: [row]))
        with principal():
            public = server.get_drift()[0].model_dump()
    else:
        public = server._row_to_history(row).model_dump()
    payload = public if path.startswith("/api/audit/") else [public]
    monkeypatch.setattr(mcp_server, "_remote_open", lambda *args, **kwargs: response(payload))
    result = mcp_server._proxy_get("https://upstream.invalid", path)
    restored = result if isinstance(result, dict) else result[0]
    assert restored["verdict"] == "rewrite"
    assert restored["C"] == 2 / 3
    assert restored["requires_review"] is True


def test_proxy_does_not_promote_rounded_or_malformed_history_envelopes(monkeypatch):
    public = server._row_to_history(fraction_row()).model_dump()
    public["C"] = round(public["C"], 4)
    monkeypatch.setattr(mcp_server, "_remote_open", lambda *args, **kwargs: response([public]))
    restored, = mcp_server._proxy_get("https://upstream.invalid", "/api/history")
    assert restored["verdict"] == "degraded"
    assert restored["C"] is None


@pytest.mark.parametrize("data", [b'{"a":1,"a":2}', b'{"C":NaN}', b'{"C":1e999}',
                                  b"x" * (2 * 1024 * 1024 + 1)])
def test_proxy_rejects_ambiguous_or_oversized_responses(monkeypatch, data):
    monkeypatch.setattr(mcp_server, "_remote_open", lambda *args, **kwargs: response(data))
    result = mcp_server._proxy_get("https://upstream.invalid", "/api/history")
    assert result == {"error": "remote_api_error:ValueError"}


def test_allowed_cors_preflight_preserves_real_request_authentication_and_owner_scope(
    tmp_path, monkeypatch,
):
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [{
        "user_id": "alice", "token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
        "enabled": True,
    }]}))
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    monkeypatch.setenv("UGH_USAGE_DB", str(tmp_path / "usage.db"))
    db = AuditDB(tmp_path / "audits.db")
    own = db.save(owner_id="alice", question="alice-private", response="alice-response")
    db.save(owner_id="bob", question="bob-private", response="bob-response")
    server.configure(db=db)
    try:
        with TestClient(server.app) as client:
            preflight = client.options("/api/audit", headers={
                "Origin": "https://chatgpt.com", "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            })
            assert preflight.status_code == 200
            assert preflight.headers["Access-Control-Allow-Origin"] == "https://chatgpt.com"
            denied = client.get("/api/history", headers={"Origin": "https://chatgpt.com"})
            assert denied.status_code == 401
            assert denied.headers["Access-Control-Allow-Origin"] == "https://chatgpt.com"
            assert "no-store" in denied.headers["Cache-Control"]
            allowed = client.get("/api/history", headers={
                "Origin": "https://chatgpt.com", "Authorization": "Bearer " + TOKEN,
            })
            assert allowed.status_code == 200
            assert [row["id"] for row in allowed.json()] == [own]
            assert "bob-private" not in allowed.text
            assert "no-store" in allowed.headers["Cache-Control"]
    finally:
        server.configure()
