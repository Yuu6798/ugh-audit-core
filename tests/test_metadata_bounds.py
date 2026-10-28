"""Bounded optional metadata and strict provider envelope parsing."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from ugh_audit import _semantic_v2 as v2, pipeline
from ugh_audit._semantic_http import AnthropicHTTP
from ugh_audit.semantic_matching import SemanticJudge


ANSWER = "受付へ資料を提出してください。"
REQUIREMENT = "受付へ資料を提出する。"


def nested_json(depth):
    value = "optional diagnostic extension"
    for _ in range(depth):
        value = [value]
    # This is accepted JSON, below the JSON parser's recursion bound.
    return json.loads(json.dumps(value))


def legacy_stub(**kwargs):
    return {"matched_id": None}


@pytest.mark.parametrize("field", ["core_propositions", "original_core_propositions"])
def test_deep_optional_metadata_cannot_block_valid_requirements(
    monkeypatch, semantic_complete_judge, field,
):
    meta = {field: [REQUIREMENT], "diagnostic_extension": nested_json(500)}
    observed = []

    def diagnostics(**kwargs):
        observed.append(kwargs["question_meta"])
        # Diagnostics receive an independent copy of the bounded context.
        kwargs["question_meta"][field].append("diagnostic mutation")
        return legacy_stub()

    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", diagnostics)
    result = pipeline.run_audit(
        question="提出方法", response=ANSWER, reference=None, question_meta=meta,
        session_id=None, semantic_judge=semantic_complete_judge,
    )
    assert result["verdict"] == "accept" and result["C"] == 1.0
    assert result["semantic_matching"]["requirements_total"] == 1
    assert result["_question_meta"][field] == [REQUIREMENT]
    assert meta[field] == [REQUIREMENT]
    assert observed
    json.dumps(result, allow_nan=False)


def test_rest_deep_optional_metadata_returns_scoped_success(monkeypatch, semantic_complete_judge):
    from fastapi.testclient import TestClient
    from ugh_audit import server

    monkeypatch.setattr(server, "_get_db", lambda: SimpleNamespace(save=lambda **kwargs: 1))
    monkeypatch.setattr(server, "_get_golden", lambda: SimpleNamespace(find_reference=lambda q: None))
    monkeypatch.setattr(server, "_make_semantic_judge", lambda: semantic_complete_judge)
    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", legacy_stub)
    payload = dict(question="提出方法", response=ANSWER,
                   question_meta={"core_propositions": [REQUIREMENT],
                                  "diagnostic_extension": nested_json(500)})
    client = TestClient(server.app, raise_server_exceptions=False)
    response = client.post("/api/audit", content=json.dumps(payload),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 200
    result = response.json()
    assert result["verdict"] == "accept" and result["C"] == 1.0
    assert result["requires_review"] is False


@pytest.mark.parametrize("requirements", [
    [nested_json(500)],
    (REQUIREMENT,),
    {"0": REQUIREMENT},
    [REQUIREMENT, None],
    [REQUIREMENT] * 17,
])
def test_metadata_normalization_never_certifies_invalid_requirements(monkeypatch, requirements):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid requirements must not invoke the semantic checker")

    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", legacy_stub)
    result = pipeline.run_audit(
        question="提出方法", response=ANSWER, reference=None,
        question_meta={"core_propositions": requirements}, session_id=None,
        semantic_judge=forbidden,
    )
    assert result["verdict"] == "degraded" and result["C"] is None
    assessment = result["semantic_matching"]
    assert assessment["fulfilled_count"] == 0
    if isinstance(requirements, list):
        assert assessment["requirements_total"] == len(requirements)
        assert assessment["unknown_count"] == len(requirements)


@pytest.mark.parametrize("failure", ["deadline", "fallback"])
def test_bounded_metadata_does_not_promote_expired_or_fallback_assessments(monkeypatch, failure):
    def forbidden(*args, **kwargs):
        pytest.fail("Expired or fallback assessments must not invoke the semantic checker")

    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", legacy_stub)
    monkeypatch.setattr("experiments.meta_generator.generate_meta", lambda question: {
        "core_propositions": [REQUIREMENT], "_is_fallback": True,
    })
    meta = {"core_propositions": [] if failure == "fallback" else [REQUIREMENT],
            "diagnostic_extension": nested_json(500)}
    result = pipeline.run_audit(
        question="提出方法", response=ANSWER, reference=None, question_meta=meta,
        session_id=None, semantic_judge=forbidden,
        auto_generate_meta=failure == "fallback",
        semantic_deadline=time.monotonic() - 1 if failure == "deadline" else None,
    )
    assert result["verdict"] == "degraded" and result["C"] is None
    assert result["semantic_matching"]["unknown_count"] == 1


class FakeSocket:
    def settimeout(self, value):
        assert value > 0


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = body

    def getheader(self, name):
        return "req_offline_envelope"

    def read1(self, size):
        assert len(self.body) <= size
        return self.body

    def isclosed(self):
        return True


class FakeConnection:
    def __init__(self, body):
        self.sock = FakeSocket()
        self.response = FakeResponse(body)

    def connect(self):
        pass

    def request(self, *args, **kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        pass


def envelope_fields():
    raw = json.dumps(dict(status="fulfilled", missing_kind=None,
                          evidence=[ANSWER], reason="提出指示を伝えている。"))
    return ('"model":' + json.dumps(v2.MODEL) + ',"stop_reason":"end_turn",'
            '"content":[{"type":"text","text":' + json.dumps(raw) + '}]')


@pytest.mark.parametrize("extra", [
    '"model":"different-model",',
    '"usage":{"input_tokens":1,"input_tokens":2},',
    '"usage":{"input_tokens":NaN},',
    '"usage":{"input_tokens":Infinity},',
    '"usage":{"input_tokens":-Infinity},',
])
def test_invalid_outer_provider_json_cannot_certify_fulfillment(monkeypatch, extra):
    fields = envelope_fields()
    if extra.startswith('"model"'):
        body = ("{" + extra + fields + ',"usage":{}}').encode()
    else:
        body = ("{" + extra + fields + "}").encode()
    monkeypatch.setattr("ugh_audit._semantic_http.http.client.HTTPSConnection",
                        lambda *args, **kwargs: FakeConnection(body))
    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", legacy_stub)
    judge = SemanticJudge("offline-placeholder")
    result = pipeline.run_audit(
        question="提出方法", response=ANSWER, reference=None,
        question_meta={"core_propositions": [REQUIREMENT]}, session_id=None,
        semantic_judge=judge,
    )
    assert result["verdict"] == "degraded" and result["C"] is None
    row = result["semantic_matching"]["results"][0]
    assert row["status"] == "unknown" and row["reason"] == "api_error"
    assert row["error_type"] == "ValueError"


def test_valid_outer_provider_json_preserves_grounded_result(monkeypatch):
    body = ("{" + envelope_fields() + ',"usage":{}}').encode()
    monkeypatch.setattr("ugh_audit._semantic_http.http.client.HTTPSConnection",
                        lambda *args, **kwargs: FakeConnection(body))
    with AnthropicHTTP("offline-placeholder", timeout=1) as client:
        result = v2.judge(client, REQUIREMENT, ANSWER)
    assert result["status"] == "fulfilled"
    assert result["returned_model"] == v2.MODEL
    assert result["evidence"] == [ANSWER]
