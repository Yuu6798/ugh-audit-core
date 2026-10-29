"""HTTP metadata generation never falls back to host credentials or shared cache."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import logging
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from experiments import meta_cache, meta_generator
from ugh_audit import beta_access, pipeline, server
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB


HOST_KEY = "sk-ant-api03-host-sentinel-" + "h" * 48
CUSTOMER_KEY = "sk-ant-api03-customer-sentinel-" + "c" * 48
SECOND_KEY = "sk-ant-api03-second-sentinel-" + "s" * 48
QUESTION = "申請書の送付方法を案内してください。"
ANSWER = "申請書を金曜日までに担当者へ送付してください。"
GENERATED = {"core_propositions": ["申請書を金曜日までに送付する", "担当者へ申請書を送付する"],
             "trap_type": ""}


@contextmanager
def http_context(key):
    marker = beta_access._principal.set(beta_access.Principal(None, provider_key=key))
    try:
        yield
    finally:
        beta_access._principal.reset(marker)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    cache = tmp_path / "shared-cache"
    monkeypatch.setenv("UGH_META_CACHE_DIR", str(cache))
    meta_cache.save_cached_meta(QUESTION, {"core_propositions": ["private shared cached requirement"]},
                                meta_generator.DEFAULT_MODEL)
    state = SimpleNamespace(keys=[], payloads=[], closed=[], exception=None,
                            generated=copy.deepcopy(GENERATED), cache=cache,
                            model=meta_generator.DEFAULT_MODEL, stop="end_turn", blocks=None)
    state.cache_before = {path.name: path.read_bytes() for path in cache.iterdir()}

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP must not access implicit SDK credentials or shared cache")

    monkeypatch.setattr(meta_generator, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(meta_generator, "anthropic", SimpleNamespace(Anthropic=forbidden), raising=False)
    monkeypatch.setattr(meta_cache, "get_cached_meta", forbidden)
    monkeypatch.setattr(meta_cache, "save_cached_meta", forbidden)

    class Transport:
        def __init__(self, api_key, timeout):
            state.keys.append((api_key, timeout))
            self.messages = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            state.closed.append(True)

        def create(self, **payload):
            state.payloads.append(payload)
            if state.exception:
                raise state.exception
            blocks = state.blocks if state.blocks is not None else [SimpleNamespace(
                type="text", text=json.dumps(state.generated, ensure_ascii=False))]
            return SimpleNamespace(model=state.model, stop_reason=state.stop, content=blocks)

    monkeypatch.setattr("ugh_audit._semantic_http.AnthropicHTTP", Transport)
    return state


def test_http_generation_uses_only_customer_key_and_ignores_warm_shared_cache(provider):
    with http_context(CUSTOMER_KEY):
        result = meta_generator.generate_meta(QUESTION)
    assert result["core_propositions"] == GENERATED["core_propositions"]
    assert provider.keys == [(CUSTOMER_KEY, 10.0)]
    assert provider.closed == [True]
    assert len(provider.payloads) == 1
    assert provider.payloads[0]["model"] == meta_generator.DEFAULT_MODEL
    assert provider.payloads[0]["max_tokens"] == 1024
    assert CUSTOMER_KEY not in json.dumps(provider.payloads)
    assert HOST_KEY not in json.dumps(result)
    assert CUSTOMER_KEY not in json.dumps(result)
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


@pytest.mark.parametrize("key", [None, ""])
def test_http_without_customer_key_cannot_call_owner_or_use_cached_generation(provider, key):
    with http_context(key):
        result = meta_generator.generate_meta(QUESTION)
    assert result["_is_fallback"] is True
    assert result["core_propositions"] == [QUESTION]
    assert provider.keys == provider.payloads == []
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


def test_sequential_http_users_never_reuse_credentials_or_generation(provider):
    for key in (CUSTOMER_KEY, SECOND_KEY):
        with http_context(key):
            assert not meta_generator.generate_meta(QUESTION).get("_is_fallback")
    assert provider.keys == [(CUSTOMER_KEY, 10.0), (SECOND_KEY, 10.0)]
    assert len(provider.payloads) == 2
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


def test_http_generator_error_has_no_credential_retry_or_private_error_log(provider, caplog):
    private_error = "private upstream body containing " + CUSTOMER_KEY + HOST_KEY + QUESTION
    provider.exception = RuntimeError(private_error)
    with caplog.at_level(logging.WARNING), http_context(CUSTOMER_KEY):
        result = meta_generator.generate_meta(QUESTION)
    assert result["_is_fallback"] is True
    assert provider.keys == [(CUSTOMER_KEY, 10.0)]
    assert len(provider.payloads) == 1
    assert provider.closed == [True]
    for value in (CUSTOMER_KEY, HOST_KEY, QUESTION, private_error):
        assert value not in caplog.text
    assert CUSTOMER_KEY not in json.dumps(result)
    assert HOST_KEY not in json.dumps(result)
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


def test_http_validation_never_logs_provider_generated_content(provider, caplog):
    private_generated = "private-generated-value"
    provider.generated.update(trap_type=private_generated,
                              disqualifying_shortcuts=[f"「{private_generated}」と全否定する"])
    with caplog.at_level(logging.WARNING), http_context(CUSTOMER_KEY):
        result = meta_generator.generate_meta(QUESTION)
    assert result["trap_type"] == ""
    assert result["disqualifying_shortcuts"] == []
    assert private_generated not in caplog.text


@pytest.mark.parametrize("fault", ["model", "truncated", "empty", "nontext", "not-object", "bad-json"])
def test_http_invalid_generation_is_unverified_and_never_retried(provider, fault):
    if fault == "model":
        provider.model = "unexpected-model"
    elif fault == "truncated":
        provider.stop = "max_tokens"
    elif fault == "empty":
        provider.blocks = []
    elif fault == "nontext":
        provider.blocks = [SimpleNamespace(type="tool_use", text="untrusted")]
    elif fault == "not-object":
        provider.generated = ["untrusted"]
    else:
        provider.blocks = [SimpleNamespace(type="text", text="unparseable")]
    with http_context(CUSTOMER_KEY):
        result = meta_generator.generate_meta(QUESTION)
    assert result["_is_fallback"] is True
    assert len(provider.payloads) == 1


@pytest.fixture
def http_service(tmp_path, monkeypatch, semantic_complete_judge):
    db = AuditDB(tmp_path / "audit.db")
    server.configure(db=db, golden=GoldenStore(tmp_path / "golden.json"))
    monkeypatch.setattr(server, "_make_semantic_judge", lambda: semantic_complete_judge)

    def omit_diagnostics(**kwargs):
        raise RuntimeError("Optional diagnostics excluded from generation regression")

    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", omit_diagnostics)
    try:
        with TestClient(server.app, base_url="http://localhost") as client:
            yield client, db
    finally:
        server.configure()


@pytest.mark.parametrize("metadata", [None, {}, {"trap_type": ""}, {"core_propositions": []}])
def test_real_http_empty_or_partial_generation_preserves_draft_review_and_safe_persistence(
    provider, http_service, metadata,
):
    client, db = http_service
    response = client.post("/api/audit", headers={"X-UGH-Anthropic-Key": CUSTOMER_KEY}, json={
        "question": QUESTION, "response": ANSWER, "question_meta": metadata,
        "auto_generate_meta": True,
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["verdict"] == "accept"
    assert result["mode"] == "computed_ai_draft"
    assert result["metadata_source"] == "llm_generated"
    assert result["requires_review"] is True
    assert result["assessment_context"]["core_propositions"] == GENERATED["core_propositions"]
    assert provider.keys == [(CUSTOMER_KEY, 10.0)]
    saved, = db.list_recent()
    for secret in (CUSTOMER_KEY, HOST_KEY):
        assert secret not in response.text
        assert secret not in json.dumps(saved)
        assert secret.encode() not in db.db_path.read_bytes()
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


def test_real_http_generation_failure_remains_unsaved_without_secret_logs(
    provider, http_service, caplog,
):
    provider.exception = RuntimeError("provider body " + CUSTOMER_KEY + HOST_KEY)
    client, db = http_service
    with caplog.at_level(logging.WARNING):
        response = client.post("/api/audit", headers={"X-UGH-Anthropic-Key": CUSTOMER_KEY}, json={
            "question": QUESTION, "response": ANSWER, "question_meta": {}, "auto_generate_meta": True,
        })
    assert response.status_code == 200
    result = response.json()
    assert result["verdict"] == "degraded"
    assert result["metadata_source"] == "fallback"
    assert result["saved_id"] is None
    assert db.list_recent() == []
    assert provider.keys == [(CUSTOMER_KEY, 10.0)]
    for secret in (CUSTOMER_KEY, HOST_KEY):
        assert secret not in caplog.text + response.text
        assert secret.encode() not in db.db_path.read_bytes()
    assert {path.name: path.read_bytes() for path in provider.cache.iterdir()} == provider.cache_before


def test_real_http_missing_customer_key_cannot_reach_generation_or_cache(provider, http_service):
    client, db = http_service
    response = client.post("/api/audit", json={
        "question": QUESTION, "response": ANSWER, "question_meta": {}, "auto_generate_meta": True,
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "provider_key_required"
    assert provider.keys == provider.payloads == []
    assert db.list_recent() == []


def test_trusted_local_experiment_keeps_cache_and_environment_compatibility(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", HOST_KEY)
    cached = {"core_propositions": ["cached local requirement"]}
    monkeypatch.setattr(meta_cache, "get_cached_meta", lambda *args: cached)
    assert meta_generator.generate_meta(QUESTION) is cached
    calls = []

    class LocalClient:
        def __init__(self):
            calls.append("local-sdk")
            self.messages = self

        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(GENERATED))])

    monkeypatch.setattr(meta_generator, "_HAS_ANTHROPIC", True)
    monkeypatch.setattr(meta_generator, "anthropic", SimpleNamespace(Anthropic=LocalClient), raising=False)
    result = meta_generator.generate_meta(QUESTION, use_cache=False)
    assert result["core_propositions"] == GENERATED["core_propositions"]
    assert calls == ["local-sdk"]
