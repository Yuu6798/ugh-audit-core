"""Public interfaces cannot revive acceptance after a missing/unknown requirement."""
from __future__ import annotations

import copy
import json

import pytest
from starlette.testclient import TestClient

from ugh_audit import mcp_server, server
from ugh_audit.reference.golden_store import GoldenStore
from ugh_audit.semantic_matching import (
    MODEL, DECISION_SCOPE, METRICS_SCOPE, assess_requirements, apply_semantic_gate,
)
from ugh_audit.storage.audit_db import AuditDB
from ugh_calculator import Evidence

ANSWER = "日報を保存し、予定をチームへ共有してください。"
META = {
    "core_propositions": ["担当者が月曜日に見直す", "日報を保存する", "予定をチームへ共有する"],
    "trap_type": "",
}


class ControlledJudge:
    """A deterministic classifier boundary, not a claim about model performance."""

    def __init__(self, statuses):
        self.statuses = statuses
        self.calls = []

    def __call__(self, requirement, answer, *, timeout_seconds):
        self.calls.append((requirement, answer, timeout_seconds))
        status = self.statuses[META["core_propositions"].index(requirement)]
        value = {
            "status": status,
            "missing_kind": "absence" if status == "missing" else None,
            "evidence": [answer] if status == "fulfilled" else [],
            "reason": "Controlled interface test judgement",
        }
        return {
            **value, "unknown_source": "model" if status == "unknown" else None,
            "requested_model": MODEL, "returned_model": MODEL, "api_called": True,
            "stop_reason": "end_turn", "raw_response": json.dumps(value, ensure_ascii=False),
        }


@pytest.fixture
def infrastructure(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UGH_REMOTE_API", raising=False)
    db = AuditDB(tmp_path / "semantic.db")
    golden = GoldenStore(tmp_path / "golden.json")
    server.configure(db=db, golden=golden)
    def detection(question_id, response, meta):
        # This exact legacy score accepts despite one missing requirement.
        return Evidence(question_id=question_id, propositions_total=3,
                        propositions_hit=2, hit_ids=[1, 2], miss_ids=[0], f4_premise=0.0)
    monkeypatch.setattr(server, "_HAS_DETECTOR", True)
    monkeypatch.setattr(server, "_detect", detection)
    monkeypatch.setattr(mcp_server, "_HAS_DETECTOR", True)
    monkeypatch.setattr(mcp_server, "_detect", detection)
    with TestClient(server.app) as client:
        yield client, db
    server.configure(db=None, golden=None)


def install_judge(monkeypatch, statuses):
    judge = ControlledJudge(statuses)
    monkeypatch.setattr(server, "_make_semantic_judge", lambda: judge)
    monkeypatch.setattr(mcp_server, "_make_semantic_judge", lambda: judge)
    return judge


@pytest.mark.parametrize("statuses,verdict,requirement_verdict", [
    (("fulfilled", "fulfilled", "fulfilled"), "accept", "complete"),
    (("missing", "fulfilled", "fulfilled"), "rewrite", "incomplete"),
    (("unknown", "fulfilled", "fulfilled"), "degraded", "undetermined"),
    (("missing", "unknown", "fulfilled"), "degraded", "incomplete"),
])
def test_rest_and_local_mcp_have_the_same_authoritative_gate(
    infrastructure, monkeypatch, statuses, verdict, requirement_verdict,
):
    client, db = infrastructure
    install_judge(monkeypatch, statuses)
    payload = {"question": "三つの指示を伝えてください。", "response": ANSWER,
               "question_meta": copy.deepcopy(META)}
    rest = client.post("/api/audit", json=payload)
    assert rest.status_code == 200
    rest = rest.json()
    mcp = mcp_server.audit_answer(**payload)
    for result in (rest, vars(mcp)):
        assert result["schema_version"] == "4.0.0"
        assert result["decision_scope"] == DECISION_SCOPE
        assert result["metrics_scope"] == METRICS_SCOPE
        assert result["assessment_context"]["core_propositions"] == META["core_propositions"]
        assert result["S"] is result["delta_e"] is result["quality_score"] is None
        assert result["structural_gate"]["binding"] is False
        assert result["verdict"] == verdict
        assert result["verdict_advisory"] == verdict
        assert result["requirement_verdict"] == requirement_verdict
        assert result["requires_review"] is (requirement_verdict != "complete")
        assert result["legacy_metrics"]["verdict"] == "accept"
        assert result["legacy_metrics"]["C"] == pytest.approx(0.6667)
        assert result["semantic_matching"]["requirements_total"] == 3
        if "unknown" in statuses:
            assert result["mode"] == "degraded"
            assert result["is_reliable"] is False
            assert result["C"] is result["delta_e"] is result["quality_score"] is None
            assert result["saved_id"] is None
    if "unknown" in statuses:
        assert db.list_recent() == []
    else:
        saved = db.get_by_id(rest["saved_id"])
        assert saved["verdict"] == verdict
        assert saved["semantic_matching"] == rest["semantic_matching"]
        assert saved["legacy_metrics"] == rest["legacy_metrics"]
        history = client.get(f"/api/audit/{rest['saved_id']}").json()
        assert history["requirement_verdict"] == requirement_verdict
        assert history["verdict"] == verdict
        assert client.get("/api/history").json()[0]["requires_review"] == rest["requires_review"]


def test_missing_checker_cannot_be_disabled_or_replaced_by_metadata(infrastructure):
    client, db = infrastructure
    meta = {**META, "semantic_matching_enabled": False, "requires_review": False,
            "requirement_verdict": "complete", "semantic_matching": {"fulfilled_count": 3}}
    result = client.post("/api/audit", json={
        "question": "Q", "response": ANSWER, "question_meta": meta,
        "semantic_matching_enabled": False,
    }).json()
    assert result["verdict"] == "degraded"
    assert result["requirement_verdict"] == "undetermined"
    assert result["semantic_matching"]["unknown_count"] == 3
    assert result["requires_review"] is True
    assert result["saved_id"] is None
    assert db.list_recent() == []


def test_explicit_empty_canonical_requirements_do_not_fall_back(infrastructure, monkeypatch):
    client, _ = infrastructure
    judge = install_judge(monkeypatch, ("fulfilled",) * 3)
    result = client.post("/api/audit", json={
        "question": "Q", "response": ANSWER,
        "question_meta": {**META, "core_propositions": [],
                          "original_core_propositions": META["core_propositions"]},
    }).json()
    assert result["verdict"] == "degraded"
    assert result["semantic_matching"]["requirements_total"] == 0
    assert judge.calls == []


def remote_frame(statuses=("fulfilled",) * 3):
    assessment = assess_requirements(META, ANSWER, ControlledJudge(statuses))
    source = {"schema_version": "4.0.0", "S": 1.0, "C": 1.0, "delta_e": 0.0,
            "quality_score": 5.0, "verdict": "accept", "verdict_advisory": "accept",
            "mode": "computed", "is_reliable": True, "metadata_source": "inline",
            "structural_gate": {"f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": 0.0,
                                "gate_verdict": "pass", "primary_fail": "none"},
            "semantic_matching": assessment}
    result = apply_semantic_gate(source, assessment, response=ANSWER, question_meta=META)
    result["assessment_context"] = copy.deepcopy(META)
    return result


def fake_remote(monkeypatch, result):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self, size=-1):
            return json.dumps(result, ensure_ascii=False).encode()
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())


@pytest.mark.parametrize("failure", [
    "legacy", "summary", "ids", "text", "quote", "model", "raw", "stop", "metrics",
    "unreliable", "scope", "schema", "binding", "not_called",
])
def test_proxy_revalidates_remote_acceptance_against_the_actual_request(monkeypatch, failure):
    frame = remote_frame()
    if failure == "legacy":
        del frame["semantic_matching"]
    elif failure == "summary":
        frame["semantic_matching"]["results"] = []
    elif failure == "ids":
        frame["semantic_matching"]["results"][2]["requirement_id"] = "0"
    elif failure == "text":
        frame["semantic_matching"]["results"][0]["requirement"] = "a different requirement"
    elif failure == "quote":
        frame["semantic_matching"]["results"][0]["evidence"] = ["fabricated quote"]
    elif failure == "model":
        frame["semantic_matching"]["model"] = "other-model"
    elif failure == "raw":
        frame["semantic_matching"]["results"][0]["raw_response"] = "{}"
    elif failure == "stop":
        frame["semantic_matching"]["results"][0]["stop_reason"] = "max_tokens"
    elif failure == "unreliable":
        frame["is_reliable"] = False
    elif failure == "scope":
        frame["metrics_scope"] = "claimed_quality"
    elif failure == "schema":
        frame["schema_version"] = "3.0.0"
    elif failure == "binding":
        frame["structural_gate"]["binding"] = True
    elif failure == "not_called":
        frame["semantic_matching"]["results"][0]["api_called"] = False
    else:
        frame["S"] = float("nan")
    fake_remote(monkeypatch, frame)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER,
                                    question_meta=META)
    assert result.verdict == result.verdict_advisory == "degraded"
    assert result.requires_review is True
    assert result.is_reliable is False
    assert result.C is result.delta_e is result.quality_score is None


@pytest.mark.parametrize("statuses,expected", [
    (("missing", "fulfilled", "fulfilled"), "rewrite"),
    (("unknown", "fulfilled", "fulfilled"), "degraded"),
    (("fulfilled",) * 3, "accept"),
])
def test_proxy_recomputes_top_level_summary_not_remote_flags(monkeypatch, statuses, expected):
    frame = remote_frame(statuses)
    frame.update(requirement_verdict="complete", requires_review=False)
    fake_remote(monkeypatch, frame)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER,
                                    question_meta=META)
    assert result.verdict == expected
    assert result.requires_review is (expected != "accept")


def test_legacy_history_is_guarded_without_overwriting_original_records(infrastructure):
    client, db = infrastructure
    row_id = db.save(question="OldQ", response="OldR", S=1.0, C=1.0, delta_e=0.0,
                     quality_score=5.0, verdict="accept", session_id="old")
    for row in (db.get_by_id(row_id), db.list_recent()[0], db.drift_history()[0],
                client.get(f"/api/audit/{row_id}").json(), client.get("/api/history").json()[0],
                client.get("/api/drift").json()[0], mcp_server.get_audit(row_id)):
        assert row["verdict"] == "degraded"
        assert row["requirement_verdict"] == "undetermined"
        assert row["requires_review"] is True
        assert row["legacy_metrics"]["verdict"] == "accept"
    with db._conn() as conn:
        stored = conn.execute("SELECT verdict,C FROM audit_runs WHERE id=?", (row_id,)).fetchone()
    assert stored == ("accept", 1.0)
    summary = client.get("/api/session/old").json()
    assert summary["verified_total"] == 0
    assert summary["unverified_total"] == 1
    assert summary["avg_delta_e"] is None
    assert summary["legacy_summary"]["avg_delta_e"] == 0.0


def test_remote_history_cannot_resurrect_an_unchecked_accept(monkeypatch):
    fake_remote(monkeypatch, {"id": 1, "verdict": "accept", "S": 1.0, "C": 1.0,
                              "delta_e": 0.0, "quality_score": 5.0, "response": ANSWER})
    result = mcp_server._proxy_get("https://example.invalid", "/api/audit/1")
    assert result["verdict"] == "degraded"
    assert result["requires_review"] is True


def test_verified_session_statistics_exclude_unchecked_legacy_scores(infrastructure):
    client, db = infrastructure
    db.save(session_id="mixed", response="old", S=1, C=1, delta_e=0.8,
            quality_score=1.8, verdict="accept")
    assessment = assess_requirements(META, ANSWER, ControlledJudge(("fulfilled",) * 3))
    db.save(session_id="mixed", response=ANSWER, S=1, C=1, delta_e=0.0,
            quality_score=5.0, verdict="accept", semantic_matching=assessment,
            requirement_verdict="complete", requires_review=False,
            schema_version="4.0.0", decision_scope=DECISION_SCOPE,
            metrics_scope=METRICS_SCOPE, question_meta=META)
    summary = client.get("/api/session/mixed").json()
    assert summary["total"] == 2
    assert summary["verified_total"] == summary["unverified_total"] == 1
    assert summary["avg_S"] is summary["avg_delta_e"] is summary["avg_quality_score"] is None
    assert summary["legacy_summary"]["avg_delta_e"] == 0.4


def test_remote_legacy_session_statistics_are_not_reported_as_verified(monkeypatch):
    fake_remote(monkeypatch, {"session_id": "old", "total": 3,
                              "avg_delta_e": 0.01, "avg_quality_score": 4.96})
    result = mcp_server._proxy_get("https://example.invalid", "/api/session/old")
    assert result["verified_total"] == 0
    assert result["unverified_total"] == 3
    assert result["avg_delta_e"] is None
    assert result["legacy_summary"]["avg_delta_e"] == 0.01


def test_session_with_missing_and_unknown_is_still_unverified(infrastructure):
    _, db = infrastructure
    assessment = assess_requirements(
        META, ANSWER, ControlledJudge(("missing", "unknown", "fulfilled")),
    )
    db.save(session_id="mixed-unknown", response=ANSWER, S=1, C=1, delta_e=0.0,
            quality_score=5.0, verdict="accept", semantic_matching=assessment)
    summary = db.session_summary("mixed-unknown")
    assert summary["verified_total"] == 0
    assert summary["unverified_total"] == 1
    assert summary["avg_delta_e"] is None


def test_remote_transport_errors_do_not_echo_exception_contents(monkeypatch):
    def failure(*args, **kwargs):
        raise RuntimeError("private request body must not be logged")
    monkeypatch.setattr("urllib.request.urlopen", failure)
    result = mcp_server._proxy_audit("https://example.invalid", question="Q", response=ANSWER,
                                    question_meta=META)
    assert result.verdict == "degraded"
    assert result.semantic_matching["unknown_count"] == 3
    assert "private request body" not in json.dumps(vars(result))
