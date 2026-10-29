"""Real MCP SDK clients over loopback TCP; provider responses are controlled doubles.

These are protocol/isolation tests, not production-provider or model-quality tests.
No external model call or real credential is used.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
import hashlib
import json
import socket
import sqlite3
from threading import Barrier, Event, Thread
import time
from types import SimpleNamespace

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import pytest
import uvicorn

from experiments.prompts.meta_generation_v1 import SYSTEM_PROMPT as META_SYSTEM
from ugh_audit import _semantic_http, pipeline, semantic_matching, server
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB


HOST_KEY = "offline_host_key_never_used_" + "h" * 40
KEYS = {owner: "offline_customer_" + owner + "_" + owner[0] * 40 for owner in ("alice", "bob")}
TOKENS = {owner: "offline_invite_" + owner + "_" + owner[0] * 40 for owner in KEYS}
SESSION = "same-session-for-both-owners"


def payload(owner, **changes):
    return {"question": "Describe " + owner, "response": owner + " is present.",
            "reference": "Explicit test reference", "session_id": SESSION,
            "question_meta": {"core_propositions": [owner + " is present."]}, **changes}


@pytest.fixture
def tcp_service(tmp_path, monkeypatch):
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [
        {"user_id": owner, "token_sha256": hashlib.sha256(token.encode()).hexdigest(), "enabled": True}
        for owner, token in TOKENS.items()]}))
    monkeypatch.setenv("UGH_ACCESS_MODE", "invite")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    monkeypatch.setenv("UGH_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setenv("UGH_META_CACHE_DIR", str(tmp_path / "meta-cache"))
    monkeypatch.setenv("UGH_AUDIT_CACHE_DIR", str(tmp_path / "diagnostic-cache"))
    monkeypatch.setenv("UGH_REQUESTS_PER_MINUTE", "1000")
    for name in ("UGH_REMOTE_API", "UGH_REMOTE_TOKEN", "UGH_INVITES_JSON",
                 "UGH_AUDITS_PER_DAY", "UGH_GLOBAL_AUDITS_PER_DAY",
                 "UGH_CONCURRENT_PER_USER", "UGH_CONCURRENT_GLOBAL"):
        monkeypatch.delenv(name, raising=False)
    state = SimpleNamespace(directory=tmp_path, registry=registry, calls=[], outputs=[],
                            statuses=[], barrier=None, server_errors=[])

    class ControlledProvider:
        def __init__(self, key, timeout):
            assert key in KEYS.values(), "No HTTP request may select the host credential"
            self.key, self.messages = key, self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **request):
            owner = next(owner for owner, key in KEYS.items() if key == self.key)
            if request["system"] == META_SYSTEM:
                assert "Describe " + owner in request["messages"][0]["content"]
                state.calls.append((self.key, "generation", owner))
                value = {"core_propositions": [owner + " is present."], "trap_type": ""}
            else:
                content = json.loads(request["messages"][0]["content"])
                assert content["answer"] == owner + " is present.", "Caller context crossed owners"
                state.calls.append((self.key, "semantic", owner))
                if state.barrier is not None:
                    state.barrier.wait(timeout=10)
                value = {"status": "fulfilled", "missing_kind": None,
                         "evidence": [content["answer"]], "reason": "Controlled protocol test proof"}
            return SimpleNamespace(model=request["model"], stop_reason="end_turn",
                                   content=[SimpleNamespace(type="text", text=json.dumps(value))])

    monkeypatch.setattr(_semantic_http, "AnthropicHTTP", ControlledProvider)
    monkeypatch.setattr(semantic_matching, "AnthropicHTTP", ControlledProvider)

    def omit_optional_diagnostics(**kwargs):
        raise RuntimeError("Optional local model loading excluded from MCP protocol tests")

    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", omit_optional_diagnostics)
    state.db = AuditDB(tmp_path / "audit.db")
    server.configure(db=state.db, golden=GoldenStore(tmp_path / "golden.json"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    state.url = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp/"
    process = uvicorn.Server(uvicorn.Config(
        server.app, loop="asyncio", lifespan="on", log_config=None, log_level="error",
        access_log=False, timeout_keep_alive=1, timeout_graceful_shutdown=3,
    ))

    def serve():
        try:
            process.run(sockets=[listener])
        except BaseException as exc:
            state.server_errors.append(type(exc).__name__)

    thread = Thread(target=serve, name="controlled-mcp-tcp-test", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not process.started:
            assert thread.is_alive() and not state.server_errors, "Local ASGI server failed to start"
            assert time.monotonic() < deadline, "Local ASGI server startup timed out"
            Event().wait(0.01)
        yield state
    finally:
        process.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            process.force_exit = True
            thread.join(timeout=5)
        listener.close()
        server.configure()
        assert not thread.is_alive(), "Local ASGI listener must terminate after the test"
        assert not state.server_errors


@asynccontextmanager
async def connected(state, owner, *, provider=True):
    headers = {"Authorization": "Bearer " + TOKENS[owner]}
    if provider:
        headers["X-UGH-Anthropic-Key"] = KEYS[owner]

    async def observe(response):
        state.statuses.append((owner, response.request.method, response.status_code))

    async with httpx.AsyncClient(headers=headers, timeout=15, trust_env=False,
                                 event_hooks={"response": [observe]}) as http_client:
        async with streamable_http_client(state.url, http_client=http_client) as (read, write, _):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as client:
                initialized = await client.initialize()
                state.outputs.append(initialized.model_dump_json())
                assert initialized.serverInfo.name == "UGH Audit"
                yield client


async def call(state, client, tool, arguments=None):
    result = await client.call_tool(tool, arguments or {})
    state.outputs.append(result.model_dump_json())
    if result.isError:
        return result
    structured = result.structuredContent
    assert structured is not None, "The real MCP client must receive structured tool output"
    return structured["result"] if set(structured) == {"result"} else structured


def assert_no_credentials(state, log_text):
    outputs = "\n".join(state.outputs) + log_text
    for secret in (HOST_KEY, *KEYS.values(), *TOKENS.values()):
        assert secret not in outputs
        for path in state.directory.rglob("*"):
            if path.is_file():
                assert secret.encode() not in path.read_bytes()
    assert not (state.directory / "meta-cache").exists()


def test_real_mcp_tcp_clients_with_controlled_provider_isolate_concurrent_owners(tcp_service, caplog):
    state = tcp_service

    async def scenario():
        async with connected(state, "alice") as alice, connected(state, "bob") as bob:
            tools = await alice.list_tools()
            state.outputs.append(tools.model_dump_json())
            definitions = {tool.name: tool for tool in tools.tools}
            assert {"audit_answer", "get_history", "get_audit", "get_session_summary",
                    "get_drift_history"}.issubset(definitions)
            assert "auto_generate_meta" in definitions["audit_answer"].inputSchema["properties"]
            assert all("key" not in name.lower() and "credential" not in name.lower()
                       for tool in tools.tools for name in tool.inputSchema.get("properties", {}))
            assert definitions["audit_answer"].outputSchema is not None
            results = {}
            state.barrier = Barrier(2)

            async def audit(owner, client):
                results[owner] = await call(state, client, "audit_answer", payload(owner))

            async with anyio.create_task_group() as group:
                group.start_soon(audit, "alice", alice)
                group.start_soon(audit, "bob", bob)
            state.barrier = None
            assert {result["verdict"] for result in results.values()} == {"accept"}
            assert results["alice"]["saved_id"] != results["bob"]["saved_id"]
            assert set(state.calls) == {(KEYS[owner], "semantic", owner) for owner in KEYS}
            for owner, other, client in (("alice", "bob", alice), ("bob", "alice", bob)):
                history = await call(state, client, "get_history")
                assert [row["id"] for row in history] == [results[owner]["saved_id"]]
                own = await call(state, client, "get_audit", {"audit_id": results[owner]["saved_id"]})
                assert own["response"] == owner + " is present."
                foreign = await call(state, client, "get_audit", {"audit_id": results[other]["saved_id"]})
                missing = await call(state, client, "get_audit", {"audit_id": 999999})
                assert "error" in foreign and "error" in missing
                assert "response" not in foreign
                summary = await call(state, client, "get_session_summary", {"session_id": SESSION})
                assert summary["total"] == summary["verified_total"] == 1
                drift = await call(state, client, "get_drift_history")
                assert [row["response"] for row in drift] == [owner + " is present."]
                denied = await call(state, client, "audit_answer", payload(
                    owner, retry_of=results[other]["saved_id"]))
                assert denied.isError and "audit_not_found" in denied.model_dump_json()

    anyio.run(scenario)
    with sqlite3.connect(state.directory / "usage.db") as connection:
        assert connection.execute("SELECT SUM(attempts) FROM beta_daily_audits").fetchone() == (2,)
    assert len(state.calls) == 2
    assert_no_credentials(state, caplog.text)


def test_real_mcp_tcp_generated_empty_and_partial_context_uses_each_customer_key(tcp_service, caplog):
    state = tcp_service

    async def scenario():
        for owner, metadata in (("alice", {}), ("bob", {"trap_type": ""})):
            async with connected(state, owner) as client:
                result = await call(state, client, "audit_answer", payload(
                    owner, question_meta=metadata, auto_generate_meta=True))
                assert result["verdict"] == "accept"
                assert result["requires_review"] is True
                assert result["metadata_source"] == "llm_generated"
                assert result["mode"] == "computed_ai_draft"
                assert result["assessment_context"]["core_propositions"] == [owner + " is present."]
                history = await call(state, client, "get_history")
                assert [row["id"] for row in history] == [result["saved_id"]]
                assert history[0]["requires_review"] is True

    anyio.run(scenario)
    assert set(state.calls) == {(KEYS[owner], kind, owner)
                               for owner in KEYS for kind in ("generation", "semantic")}
    assert len(state.calls) == 4
    assert_no_credentials(state, caplog.text)


def test_real_mcp_tcp_missing_provider_key_and_revoked_initialized_invite_are_rejected(
    tcp_service, caplog,
):
    state = tcp_service

    async def scenario():
        async with connected(state, "alice", provider=False) as client:
            assert (await client.list_tools()).tools
            denied = await call(state, client, "audit_answer", payload("alice"))
            assert denied.isError and "provider_key_required" in denied.model_dump_json()
        initialized = False
        failure = None
        try:
            async with connected(state, "alice") as client:
                assert (await client.list_tools()).tools
                initialized = True
                registry = json.loads(state.registry.read_text())
                registry["users"][0]["enabled"] = False
                state.registry.write_text(json.dumps(registry))
                try:
                    await client.list_tools()
                except Exception as exc:
                    failure = exc
        except Exception as exc:
            failure = failure or exc
        assert initialized, "Revocation must be checked on an already initialized client"
        assert failure is not None
        assert ("alice", "POST", 401) in state.statuses
        async with connected(state, "bob") as other:
            assert await call(state, other, "get_history") == []

    anyio.run(scenario)
    assert state.calls == []
    assert state.db.list_recent() == []
    assert_no_credentials(state, caplog.text)
