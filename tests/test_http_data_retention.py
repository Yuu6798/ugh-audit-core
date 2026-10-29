"""Private HTTP diagnostic inputs never enter shared embedding caches or logs."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import cascade_matcher
import grv_calculator
from ugh_audit import beta_access, pipeline
from ugh_audit.reference.golden_store import GoldenEntry, GoldenStore


PRIVATE_TEXT = "private-audit-requirement-sentinel"
PRIVATE_ID = "private-question-id-sentinel"
PROVIDER_KEY = "synthetic-provider-key-sentinel-1234567890"
INVITE_TOKEN = "synthetic-invite-token-sentinel-1234567890"


class Encoder:
    """Controlled vectors without a model download or provider request."""
    def __init__(self, value=1.0, failure=False):
        self.value = value
        self.failure = failure
        self.calls = []

    def encode(self, texts, **kwargs):
        self.calls.append(list(texts))
        if self.failure:
            raise ValueError(" ".join([*texts, PROVIDER_KEY]))
        return np.full((len(texts), 3), self.value, dtype=np.float32)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    path = tmp_path / "embedding_cache.npz"
    monkeypatch.setattr(cascade_matcher, "_EMBED_CACHE_PATH", path)
    monkeypatch.setattr(cascade_matcher, "_CACHE_DISABLED", False)
    cascade_matcher.clear_embedding_cache()
    yield path
    cascade_matcher.clear_embedding_cache()


@pytest.fixture
def http_call(tmp_path, monkeypatch):
    """Use real request middleware and its sync-worker ContextVar propagation."""
    registry = tmp_path / "invites.json"
    registry.write_text(json.dumps({"users": [{"user_id": "alice", "token_sha256":
        hashlib.sha256(INVITE_TOKEN.encode()).hexdigest()}]}), encoding="utf-8")
    monkeypatch.setenv("UGH_INVITE_FILE", str(registry))
    monkeypatch.setenv("UGH_USAGE_DB", str(tmp_path / "usage.db"))

    def call(action, mode="invite"):
        monkeypatch.setenv("UGH_ACCESS_MODE", mode)

        def probe(request):
            assert beta_access.is_http_request()
            assert beta_access.request_provider_key() == PROVIDER_KEY
            return JSONResponse(action())

        app = Starlette(routes=[Route("/probe", probe)],
                        middleware=[Middleware(beta_access.InviteMiddleware)])
        with TestClient(app, base_url="http://localhost") as client:
            response = client.get("/probe", headers={
                "Authorization": "Bearer " + INVITE_TOKEN,
                beta_access.PROVIDER_KEY_HEADER: PROVIDER_KEY,
            })
        assert response.status_code == 200, response.text
        assert not beta_access.is_http_request()
        return response.json()

    return call


@pytest.mark.parametrize("mode", ["invite", "local"])
@pytest.mark.parametrize("existing", ["empty", "memory", "disk"])
def test_http_embeddings_do_not_read_or_write_shared_cache(
        isolated_cache, http_call, caplog, mode, existing):
    if existing != "empty":
        cascade_matcher.encode_texts_cached(Encoder(99), [PRIVATE_TEXT], model_name="test")
        cascade_matcher.flush_embedding_cache()
        if existing == "disk":
            cascade_matcher.clear_embedding_cache()
    before_bytes = isolated_cache.read_bytes() if isolated_cache.exists() else None
    before_state = cascade_matcher.embedding_cache_stats()
    before_vectors = {key: value.copy() for key, value in cascade_matcher._embedding_cache.items()}
    encoder = Encoder(7)

    def action():
        first = cascade_matcher.encode_texts_cached(encoder, [PRIVATE_TEXT], model_name="test")
        second = cascade_matcher.encode_texts_cached(encoder, [PRIVATE_TEXT], model_name="test")
        # Shape recovery calls this helper too. HTTP must not touch local state.
        cascade_matcher.invalidate_embedding_cache(reason=PRIVATE_TEXT)
        assert np.all(first == 7) and np.all(second == 7)
        return {"encoded": len(encoder.calls)}

    assert http_call(action, mode) == {"encoded": 2}
    assert encoder.calls == [[PRIVATE_TEXT], [PRIVATE_TEXT]]
    assert cascade_matcher.embedding_cache_stats() == before_state
    assert set(cascade_matcher._embedding_cache) == set(before_vectors)
    for key, value in before_vectors.items():
        np.testing.assert_array_equal(cascade_matcher._embedding_cache[key], value)
    # The shutdown hook runs outside request context: it still must not persist
    # an HTTP vector after the caller has disconnected.
    cascade_matcher._save_embedding_cache()
    assert (isolated_cache.read_bytes() if isolated_cache.exists() else None) == before_bytes
    assert PRIVATE_TEXT not in caplog.text


def test_http_detector_failure_logs_no_id_input_or_exception_body(
        http_call, monkeypatch, caplog):
    monkeypatch.setattr(grv_calculator, "compute_grv", lambda **kwargs: None)

    def failing_detector(question_id, response, question_meta):
        raise RuntimeError(" ".join([question_id, response, PROVIDER_KEY]))

    def action():
        result = pipeline._run_legacy_diagnostics(
            question=PRIVATE_TEXT, response=PRIVATE_TEXT, reference=None, session_id=None,
            question_meta={"id": PRIVATE_ID, "core_propositions": [PRIVATE_TEXT]},
            detect_fn=failing_detector,
        )
        assert "detector_error:RuntimeError" in result["errors"]
        return {"handled": True}

    assert http_call(action) == {"handled": True}
    assert "HTTP detector failed: RuntimeError" in caplog.text
    for secret in (PRIVATE_TEXT, PRIVATE_ID, PROVIDER_KEY, INVITE_TOKEN):
        assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_http_golden_encoder_failure_logs_only_error_type(
        http_call, isolated_cache, monkeypatch, caplog):
    monkeypatch.setattr(cascade_matcher, "get_shared_model", lambda: Encoder(failure=True))
    store = object.__new__(GoldenStore)
    entry = GoldenEntry(question="Public reference", reference="Public answer", source="test")

    def action():
        assert store._sbert_rerank(PRIVATE_TEXT, [(1.0, entry)]) is None
        return {"handled": True}

    assert http_call(action) == {"handled": True}
    assert "GoldenStore SBert rerank failed: ValueError" in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    assert PROVIDER_KEY not in caplog.text
    assert not isolated_cache.exists()


def test_http_model_load_failure_does_not_log_exception_body(http_call, monkeypatch, caplog):
    monkeypatch.setattr(cascade_matcher, "_shared_model", None)
    monkeypatch.setattr(cascade_matcher, "_HAS_SBERT", True)
    monkeypatch.setattr(cascade_matcher, "_shared_load_failures", 0)

    def fail():
        raise RuntimeError(PRIVATE_TEXT + PROVIDER_KEY)

    monkeypatch.setattr(cascade_matcher, "load_model", fail)

    def action():
        assert cascade_matcher.get_shared_model() is None
        return {"handled": True}

    assert http_call(action) == {"handled": True}
    assert "shared SBert model load failed (attempt 1/3): RuntimeError" in caplog.text
    assert PRIVATE_TEXT not in caplog.text
    assert PROVIDER_KEY not in caplog.text
