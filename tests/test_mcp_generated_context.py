"""Exercise real REST/local-MCP preparation and proxy revalidation without provider calls."""
from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from ugh_audit import mcp_server, pipeline, server
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.storage.audit_db import AuditDB


QUESTION = "申請書の送付方法を案内してください。"
ANSWER = "申請書を金曜日までに担当者へ送付してください。"
GENERATED = {"core_propositions": ["申請書を金曜日までに送付する", "担当者へ申請書を送付する"],
             "trap_type": ""}
ABSENT_REQUIREMENTS = [
    pytest.param(None, id="none"),
    pytest.param({}, id="empty"),
    pytest.param({"trap_type": ""}, id="partial"),
    pytest.param({"core_propositions": []}, id="empty-requirements"),
    pytest.param({"core_propositions": None}, id="null-requirements"),
    pytest.param({"core_propositions": "not-a-list"}, id="wrong-container"),
    pytest.param({"original_core_propositions": []}, id="empty-original-requirements"),
    pytest.param({"core_propositions": [], "original_core_propositions": ["old alias"]},
                 id="empty-canonical-takes-precedence"),
]


@pytest.fixture
def stack(tmp_path, monkeypatch, semantic_complete_judge):
    state = SimpleNamespace(generation="success", second_status="fulfilled",
                            calls=[], checks=[], remote=[], tamper=None)
    db = AuditDB(tmp_path / "audits.db")
    server.configure(db=db, golden=GoldenStore(tmp_path / "golden.json"))
    monkeypatch.delenv("UGH_REMOTE_API", raising=False)
    monkeypatch.delenv("UGH_REMOTE_TOKEN", raising=False)

    def generate(question):
        state.calls.append(question)
        if state.generation == "error":
            raise RuntimeError("controlled generator failure")
        if state.generation == "empty":
            return {}
        if state.generation == "invalid":
            return {"core_propositions": [""]}
        return {**copy.deepcopy(GENERATED), "_is_fallback": state.generation == "fallback"}

    def judge(requirement, answer, *, timeout_seconds):
        state.checks.append(requirement)
        result = semantic_complete_judge(requirement, answer, timeout_seconds=timeout_seconds)
        if requirement == GENERATED["core_propositions"][1] and state.second_status != "fulfilled":
            status = state.second_status
            result.update(status=status, missing_kind="absence" if status == "missing" else None,
                          evidence=[], unknown_source="model" if status == "unknown" else None)
            result["raw_response"] = json.dumps({
                name: result[name] for name in ("status", "missing_kind", "evidence", "reason")})
        return result

    def omit_optional_diagnostics(**kwargs):
        # Exercise the real diagnostic-failure fallback, avoiding optional model loading.
        raise RuntimeError("optional diagnostics excluded from context regression")

    monkeypatch.setattr("experiments.meta_generator.generate_meta", generate)
    monkeypatch.setattr(server, "_make_semantic_judge", lambda: judge)
    monkeypatch.setattr(mcp_server, "_make_semantic_judge", lambda: judge)
    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", omit_optional_diagnostics)
    try:
        with TestClient(server.app, base_url="http://localhost") as client:
            def remote_open(request, timeout):
                payload = json.loads(request.data)
                reply = client.post("/api/audit", json=payload)
                assert reply.status_code == 200, reply.text
                frame = reply.json()
                state.remote.append(copy.deepcopy(frame))
                if state.tamper:
                    state.tamper(frame)
                result = io.BytesIO(json.dumps(frame, ensure_ascii=False).encode("utf-8"))
                result.status = 200
                return result

            monkeypatch.setattr(mcp_server, "_remote_open", remote_open)

            def audit(meta, *, transport="proxy", auto_generate=True):
                if transport == "proxy":
                    monkeypatch.setenv("UGH_REMOTE_API", "https://controlled-upstream.invalid")
                else:
                    monkeypatch.delenv("UGH_REMOTE_API", raising=False)
                return mcp_server.audit_answer(
                    question=QUESTION, response=ANSWER, question_meta=copy.deepcopy(meta),
                    auto_generate_meta=auto_generate,
                )

            state.audit, state.db, state.client = audit, db, client
            yield state
    finally:
        server.configure()


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("meta", ABSENT_REQUIREMENTS)
def test_generation_uses_prepared_context_across_real_rest_and_mcp(stack, transport, meta):
    result = stack.audit(meta, transport=transport)
    assert stack.calls == [QUESTION]
    assert stack.checks == GENERATED["core_propositions"]
    if transport == "proxy":
        assert stack.remote[0]["verdict"] == "accept"  # The upstream itself did succeed.
        assert result.assessment_context == stack.remote[0]["assessment_context"]
    assert result.verdict == "accept"
    assert result.C == 1.0
    assert result.metadata_source == "llm_generated"
    assert result.mode == "computed_ai_draft"
    assert result.requires_review is True
    assert result.assessment_context["core_propositions"] == GENERATED["core_propositions"]
    assert result.saved_id is not None
    saved = stack.db.get_by_id(result.saved_id)
    assert saved["assessment_context"] == result.assessment_context
    assert saved["requires_review"] is True


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("meta", ABSENT_REQUIREMENTS)
def test_missing_requirements_without_opt_in_never_generate_or_accept(stack, transport, meta):
    result = stack.audit(meta, transport=transport, auto_generate=False)
    assert stack.calls == []
    assert stack.checks == []
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.requires_review is True
    assert result.saved_id is None
    assert stack.db.list_recent() == []


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("auto_generate", [False, True])
@pytest.mark.parametrize("field", ["core_propositions", "original_core_propositions"])
def test_nonempty_caller_requirements_are_not_replaced(stack, transport, auto_generate, field):
    meta = {field: ["利用者が確認した要件"], "trap_type": ""}
    result = stack.audit(meta, transport=transport, auto_generate=auto_generate)
    assert stack.calls == []
    assert stack.checks == meta[field]
    assert result.verdict == "accept"
    assert result.metadata_source == "inline"
    assert result.requires_review is False
    assert result.assessment_context == meta


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("requirements", [[""], [42]])
def test_invalid_nonempty_requirement_list_is_not_silently_replaced(stack, transport, requirements):
    result = stack.audit({"core_propositions": requirements}, transport=transport)
    assert stack.calls == []
    assert stack.checks == []
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("generation", ["fallback", "error", "empty", "invalid"])
def test_generator_failure_or_unverified_fallback_never_becomes_accept(stack, transport, generation):
    stack.generation = generation
    result = stack.audit({"trap_type": ""}, transport=transport)
    assert stack.calls == [QUESTION]
    assert stack.checks == []
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None
    assert result.requires_review is True
    assert stack.db.list_recent() == []


@pytest.mark.parametrize("transport", ["local", "proxy"])
@pytest.mark.parametrize("status,verdict,coverage", [("missing", "rewrite", 0.5),
                                                    ("unknown", "degraded", None)])
def test_generated_requirements_preserve_missing_and_unknown_outcomes(
    stack, transport, status, verdict, coverage,
):
    stack.second_status = status
    result = stack.audit({"trap_type": ""}, transport=transport)
    assert result.verdict == verdict
    assert result.C == coverage
    assert result.requires_review is True
    assert result.metadata_source == "llm_generated"
    assert result.assessment_context["core_propositions"] == GENERATED["core_propositions"]
    assert [row["status"] for row in result.semantic_matching["results"]] == ["fulfilled", status]
    assert (result.saved_id is None) is (status == "unknown")


@pytest.mark.parametrize("fault", ["missing", "empty", "different", "inline-source", "fallback"])
def test_generated_remote_context_must_match_proof_and_generated_provenance(stack, fault):
    def tamper(frame):
        if fault == "missing":
            frame.pop("assessment_context")
        elif fault == "empty":
            frame["assessment_context"] = {}
        elif fault == "different":
            frame["assessment_context"]["core_propositions"] = ["different requirement"]
        else:
            frame["metadata_source"] = "inline" if fault == "inline-source" else "fallback"
            frame["mode"] = "computed"
            frame["requires_review"] = False

    stack.tamper = tamper
    result = stack.audit({})
    assert stack.remote[0]["verdict"] == "accept"
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None
    assert result.requires_review is True


def test_generated_remote_result_cannot_enable_generation_without_caller_opt_in(stack, monkeypatch):
    generated = stack.client.post("/api/audit", json={
        "question": QUESTION, "response": ANSWER, "auto_generate_meta": True,
    }).json()
    assert generated["verdict"] == "accept"

    def copied_result(*args, **kwargs):
        result = io.BytesIO(json.dumps(generated).encode())
        result.status = 200
        return result

    monkeypatch.setattr(mcp_server, "_remote_open", copied_result)
    result = stack.audit({}, auto_generate=False)
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None


@pytest.mark.parametrize("auto_generate", [False, True])
def test_remote_generated_origin_cannot_replace_explicit_caller_provenance(stack, auto_generate):
    def tamper(frame):
        frame.update(metadata_source="llm_generated", mode="computed_ai_draft", requires_review=True)

    stack.tamper = tamper
    result = stack.audit({"core_propositions": ["human requirement"]}, auto_generate=auto_generate)
    assert stack.calls == []
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None


def test_explicit_caller_requirements_reject_copied_proof_for_other_generated_context(stack, monkeypatch):
    generated = stack.client.post("/api/audit", json={
        "question": QUESTION, "response": ANSWER, "auto_generate_meta": True,
    }).json()
    generated.update(metadata_source="inline", mode="computed", requires_review=False)

    def copied_result(*args, **kwargs):
        result = io.BytesIO(json.dumps(generated).encode())
        result.status = 200
        return result

    monkeypatch.setattr(mcp_server, "_remote_open", copied_result)
    meta = {"original_core_propositions": ["human requirement"]}
    result = stack.audit(meta)
    assert result.verdict == "degraded"
    assert result.C is None
    assert result.saved_id is None
    assert result.assessment_context == meta
