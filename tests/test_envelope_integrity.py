"""Public transport envelopes cannot promote contradictory producer results."""
from __future__ import annotations

import copy
import json

import pytest

from ugh_audit import mcp_server
from ugh_audit.semantic_matching import (
    COVERAGE_CONTRACT, DECISION_SCOPE, METRICS_SCOPE, MODEL, PROMPT_SHA256,
)
from ugh_audit.storage.audit_db import AuditDB, guard_history_row


ANSWER = "alpha beta"
META = {"core_propositions": ["Include alpha", "Include beta"]}


def _frame(statuses=("fulfilled", "fulfilled")):
    rows = []
    for index, status in enumerate(statuses):
        value = {
            "status": status, "missing_kind": "absence" if status == "missing" else None,
            "evidence": [ANSWER.split()[index]] if status == "fulfilled" else [],
            "reason": "Independent controlled judgement",
        }
        rows.append({
            **value, "requirement_id": str(index), "requirement": META["core_propositions"][index],
            "unknown_source": "model" if status == "unknown" else None,
            "api_called": True, "requested_model": MODEL, "returned_model": MODEL,
            "stop_reason": "end_turn", "http_status": 200,
            "raw_response": json.dumps(value),
        })
    unknown = statuses.count("unknown")
    missing = statuses.count("missing")
    fulfilled = statuses.count("fulfilled")
    verdict = "degraded" if unknown else "rewrite" if missing else "accept"
    requirement_verdict = "incomplete" if missing else "undetermined" if unknown else "complete"
    coverage = None if unknown else fulfilled / 2
    diagnostic_gate = {
        "f1": 1.0, "f2": 0.0, "f3": 1.0, "f4": 0.0,
        "gate_verdict": "fail", "primary_fail": "f1",
    }
    return {
        "id": 1, "question": "Follow both instructions", "response": ANSWER,
        "created_at": "time", "schema_version": "4.0.0",
        "decision_scope": DECISION_SCOPE, "metrics_scope": METRICS_SCOPE,
        "assessment_context": copy.deepcopy(META),
        "S": None, "C": coverage, "delta_e": None, "quality_score": None,
        "verdict": verdict, "verdict_advisory": verdict,
        "mode": "degraded" if unknown else "computed", "is_reliable": not unknown,
        "metadata_source": "inline", "requirement_verdict": requirement_verdict,
        "requires_review": verdict != "accept",
        "structural_gate": {**diagnostic_gate, "binding": False},
        "legacy_metrics": {
            "S": 0.2, "C": 0.0, "delta_e": 0.9, "quality_score": 1.4,
            "verdict": "regenerate", "structural_gate": diagnostic_gate,
        },
        "semantic_matching": {
            "model": MODEL, "prompt_sha256": PROMPT_SHA256, "coverage_contract": COVERAGE_CONTRACT,
            "requirements_total": 2, "fulfilled_count": fulfilled,
            "missing_count": missing, "unknown_count": unknown, "coverage": coverage,
            "missing_ids": [str(i) for i, s in enumerate(statuses) if s == "missing"],
            "unknown_ids": [str(i) for i, s in enumerate(statuses) if s == "unknown"],
            "requirement_verdict": requirement_verdict, "requires_review": verdict != "accept",
            "results": rows,
        },
    }


def _remote(monkeypatch, payload):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-provider-key")
    monkeypatch.setenv("UGH_REMOTE_TOKEN", "offline-test-invite-token")
    calls = []

    class Response:
        status = 200

        def __init__(self, frame):
            self.frame = frame

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=-1):
            raw = json.dumps(self.frame).encode("utf-8")
            return raw if size < 0 else raw[:size]

    def remote_open(request, timeout, *, forward_provider_key=False):
        calls.append((request.get_method(), request.full_url, forward_provider_key))
        if request.full_url.endswith("/api/capabilities"):
            assert request.get_method() == "GET" and not forward_provider_key
            return Response(mcp_server.BYOK_CAPABILITIES)
        return Response(payload)

    monkeypatch.setattr(mcp_server, "_remote_open", remote_open)
    return calls


def _request(monkeypatch, frame, channel):
    path = {"audit": "/api/audit/1", "history": "/api/history", "drift": "/api/drift"}
    payload = [frame] if channel in ("history", "drift") else frame
    calls = _remote(monkeypatch, payload)
    if channel == "post":
        result = vars(mcp_server._proxy_audit(
            "https://example.invalid", question="Follow both instructions", response=ANSWER,
            question_meta=META,
        ))
        assert calls == [
            ("GET", "https://example.invalid/api/capabilities", False),
            ("POST", "https://example.invalid/api/audit", True),
        ]
        return result
    result = mcp_server._proxy_get("https://example.invalid", path[channel])
    return result[0] if isinstance(result, list) else result


@pytest.mark.parametrize("channel", ["post", "audit", "history", "drift"])
@pytest.mark.parametrize("fault", [
    "degraded", "degraded_verdict", "wrong_coverage", "draft_mode", "fallback",
    "binding", "old_scalar", "missing_masked", "unknown_masked", "proof_http_failure",
])
def test_inconsistent_remote_envelopes_fail_closed(monkeypatch, channel, fault):
    frame = _frame()
    if fault == "degraded":
        frame.update(mode="degraded", verdict="degraded", is_reliable=False, C=None)
    elif fault == "degraded_verdict":
        frame["verdict"] = "degraded"
    elif fault == "wrong_coverage":
        frame["C"] = 0.5
    elif fault == "draft_mode":
        frame["metadata_source"] = "llm_generated"
    elif fault == "fallback":
        frame["metadata_source"] = "fallback"
    elif fault == "binding":
        frame["structural_gate"]["binding"] = True
    elif fault == "old_scalar":
        frame["S"] = 0.9
    elif fault in ("missing_masked", "unknown_masked"):
        status = "missing" if fault == "missing_masked" else "unknown"
        frame["semantic_matching"] = _frame((status, "fulfilled"))["semantic_matching"]
    else:
        frame["semantic_matching"]["results"][0]["http_status"] = 500
    result = _request(monkeypatch, frame, channel)
    assert result["verdict"] == "degraded" and result["C"] is None
    assert result["is_reliable"] is False and result["requires_review"] is True
    if channel != "post":
        twice = guard_history_row(result)
        assert twice["verdict"] == "degraded" and twice["C"] is None


@pytest.mark.parametrize("channel", ["post", "audit", "history", "drift"])
@pytest.mark.parametrize("statuses,expected,coverage", [
    (("fulfilled", "fulfilled"), "accept", 1.0),
    (("missing", "fulfilled"), "rewrite", 0.5),
    (("unknown", "fulfilled"), "degraded", None),
    (("missing", "unknown"), "degraded", None),
])
def test_consistent_remote_envelopes_preserve_semantic_verdict(
    monkeypatch, channel, statuses, expected, coverage,
):
    frame = _frame(statuses)
    result = _request(monkeypatch, frame, channel)
    assert result["verdict"] == expected and result["C"] == coverage
    assert result["S"] is result["delta_e"] is result["quality_score"] is None
    assert result["structural_gate"]["binding"] is False
    assert result["legacy_metrics"]["verdict"] == "regenerate"
    assert result["semantic_matching"]["results"] == frame["semantic_matching"]["results"]
    assert result["requirement_verdict"] == frame["requirement_verdict"]


@pytest.mark.parametrize("change", [
    {"mode": "degraded"}, {"verdict": "degraded"}, {"metadata_source": "fallback"},
])
@pytest.mark.parametrize("statuses", [("fulfilled", "fulfilled"), ("missing", "fulfilled")])
def test_saved_explicitly_degraded_or_fallback_rows_never_promote(tmp_path, change, statuses):
    db = AuditDB(tmp_path / "stored.db")
    frame = _frame(statuses)
    saved = {
        "response": ANSWER, "C": frame["C"], "verdict": frame["verdict"], "mode": "computed",
        "is_reliable": True, "metadata_source": "inline", "schema_version": "4.0.0",
        "decision_scope": DECISION_SCOPE, "metrics_scope": METRICS_SCOPE,
        "question_meta": META, "semantic_matching": frame["semantic_matching"],
        "legacy_metrics": frame["legacy_metrics"], "structural_gate": frame["structural_gate"],
        **change,
    }
    row_id = db.save(**saved)
    with db._conn() as conn:
        original = conn.execute("SELECT * FROM audit_runs WHERE id=?", (row_id,)).fetchone()
    for result in (db.get_by_id(row_id), db.list_recent()[0], db.drift_history()[0]):
        assert result["verdict"] == "degraded" and result["C"] is None
        assert result["requires_review"] is True and result["is_reliable"] is False
        twice = guard_history_row(result)
        assert twice["verdict"] == "degraded" and twice["C"] is None
    with db._conn() as conn:
        assert conn.execute("SELECT * FROM audit_runs WHERE id=?", (row_id,)).fetchone() == original


def test_old_stored_diagnostics_and_default_reliability_remain_nonbinding(tmp_path):
    db = AuditDB(tmp_path / "compatible.db")
    frame = _frame()
    row_id = db.save(
        response=ANSWER, S=0.2, C=0.0, delta_e=0.9, quality_score=1.4,
        verdict="regenerate", schema_version="4.0.0", decision_scope=DECISION_SCOPE,
        metrics_scope=METRICS_SCOPE, question_meta=META,
        semantic_matching=frame["semantic_matching"], structural_gate=frame["structural_gate"],
    )
    result = db.get_by_id(row_id)
    assert result["verdict"] == "accept" and result["C"] == 1.0
    assert result["is_reliable"] is True
    assert result["legacy_metrics"]["verdict"] == "regenerate"
    assert guard_history_row(result)["verdict"] == "accept"


@pytest.mark.parametrize("statuses", [
    ("unknown", "fulfilled"), ("missing", "unknown"), ("unknown", "unknown"),
])
def test_saved_pending_proof_preserves_known_rows_and_unknown_source(tmp_path, statuses):
    db = AuditDB(tmp_path / "pending.db")
    frame = _frame(statuses)
    row_id = db.save(
        response=ANSWER, verdict="degraded", mode="degraded", is_reliable=False,
        metadata_source="inline", schema_version="4.0.0", decision_scope=DECISION_SCOPE,
        metrics_scope=METRICS_SCOPE, question_meta=META,
        semantic_matching=frame["semantic_matching"], legacy_metrics=frame["legacy_metrics"],
        structural_gate=frame["structural_gate"],
    )
    with db._conn() as conn:
        original = conn.execute("SELECT * FROM audit_runs WHERE id=?", (row_id,)).fetchone()
    for result in (db.get_by_id(row_id), db.list_recent()[0], db.drift_history()[0]):
        assert result["verdict"] == "degraded" and result["C"] is None
        assert result["requirement_verdict"] == frame["requirement_verdict"]
        assert result["semantic_matching"]["results"] == frame["semantic_matching"]["results"]
        twice = guard_history_row(result)
        assert twice["semantic_matching"]["results"] == result["semantic_matching"]["results"]
    with db._conn() as conn:
        assert conn.execute("SELECT * FROM audit_runs WHERE id=?", (row_id,)).fetchone() == original


def test_spoofed_unknown_count_cannot_clear_an_explicit_stored_failure():
    frame = _frame()
    frame.update(mode="degraded", verdict="degraded", question_meta=META)
    frame["semantic_matching"]["unknown_count"] = 1
    result = guard_history_row(frame)
    assert result["verdict"] == "degraded" and result["C"] is None
    assert all(row["status"] == "unknown" for row in result["semantic_matching"]["results"])
    assert guard_history_row(result)["verdict"] == "degraded"


def test_fallback_pending_proof_remains_entirely_unverified():
    frame = _frame(("missing", "unknown"))
    frame.update(metadata_source="fallback", question_meta=META)
    result = guard_history_row(frame)
    assert result["verdict"] == "degraded" and result["C"] is None
    assert result["semantic_matching"]["missing_count"] == 0
    assert result["semantic_matching"]["unknown_count"] == 2
    assert all(row["unknown_source"] == "system" for row in result["semantic_matching"]["results"])
