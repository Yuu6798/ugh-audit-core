"""Independent adversarial review of the fixed instruction-coverage contract."""
from __future__ import annotations

import copy
import json
import sys

import pytest

from ugh_audit import mcp_server, pipeline
import audit as python_audit
from ugh_calculator import Evidence
from ugh_audit.semantic_matching import (
    MODEL, apply_semantic_gate, assess_requirements, validate_assessment,
)


ANSWER = "受付へ資料を提出してください。控えを保管してください。"
META = {"core_propositions": ["受付へ資料を提出する", "控えを保管する"]}


def judge(statuses):
    statuses = iter(statuses)

    def run(requirement, answer, **kwargs):
        status = next(statuses)
        raw = {
            "evidence": [ANSWER] if status == "fulfilled" else [],
            "reason": "Independent controlled contract probe.",
            "status": status,
            "missing_kind": "absence" if status == "missing" else None,
        }
        return {
            **raw, "unknown_source": "model" if status == "unknown" else None,
            "api_called": True, "requested_model": MODEL, "returned_model": MODEL,
            "stop_reason": "end_turn", "raw_response": json.dumps(raw),
        }

    return run


def legacy(**updates):
    result = {
        "schema_version": "4.0.0", "S": 0.0, "C": 0.0, "delta_e": 1.0,
        "quality_score": 1.0, "verdict": "regenerate", "mode": "degraded",
        "is_reliable": False, "metadata_source": "inline",
        "structural_gate": {"f1": 1.0, "f2": 0.0, "f3": 1.0, "f4": None,
                            "gate_verdict": "fail", "primary_fail": "f1"},
        "errors": ["legacy detector failed"],
    }
    result.update(updates)
    return result


@pytest.mark.parametrize("statuses,expected,coverage", [
    (("fulfilled", "fulfilled"), "accept", 1.0),
    (("fulfilled", "missing"), "rewrite", 0.5),
    (("fulfilled", "unknown"), "degraded", None),
    (("missing", "unknown"), "degraded", None),
])
def test_review_authority_is_not_old_reliability(statuses, expected, coverage):
    assessment = assess_requirements(META, ANSWER, judge(statuses))
    result = apply_semantic_gate(legacy(), assessment, response=ANSWER, question_meta=META)
    assert result["verdict"] == expected and result["C"] == coverage
    assert result["S"] is result["delta_e"] is result["quality_score"] is None
    assert result["legacy_metrics"]["verdict"] == "regenerate"
    assert result["structural_gate"]["binding"] is False


@pytest.mark.parametrize("eligibility", [False, None, 0, 1, "true"])
def test_review_eligibility_requires_literal_true(eligibility):
    assessment = assess_requirements(META, ANSWER, judge(("fulfilled", "fulfilled")))
    result = apply_semantic_gate(legacy(), assessment, response=ANSWER,
                                 question_meta=META, eligible=eligibility)
    assert result["verdict"] == "degraded" and result["C"] is None


@pytest.mark.parametrize("requirements", [
    [META["core_propositions"][0], None],
    [META["core_propositions"][0], 7],
    [META["core_propositions"][0], {"bad": "requirement"}],
    [META["core_propositions"][0], ["nested"]],
    [META["core_propositions"][0], " "],
])
def test_review_invalid_requirement_is_never_dropped_from_denominator(requirements):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("Malformed metadata must not invoke the judge.")
    meta = {"core_propositions": requirements}
    assessment = assess_requirements(meta, ANSWER, forbidden)
    result = apply_semantic_gate(legacy(), assessment, response=ANSWER, question_meta=meta)
    assert not calls
    assert result["verdict"] == "degraded" and result["C"] is None
    assert assessment["requirements_total"] == len(requirements)
    assert assessment["unknown_count"] == len(requirements)
    assert len(result["semantic_matching"]["results"]) == len(requirements)


def test_review_boolean_coverage_is_invalid_proof_not_numeric_one():
    assessment = assess_requirements(META, ANSWER, judge(("fulfilled", "fulfilled")))
    assessment["coverage"] = True
    assert validate_assessment(assessment, response=ANSWER, question_meta=META) is False
    result = apply_semantic_gate(legacy(), assessment, response=ANSWER, question_meta=META)
    assert result["verdict"] == "degraded"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_review_nonfinite_optional_diagnostics_cannot_break_valid_json(monkeypatch, bad):
    diagnostic = legacy(
        S=bad,
        grv={"grv": bad, "grv_debug": {"nested": [bad]}},
        mode_conditioned_grv={"anchor_alignment": bad},
    )
    diagnostic["structural_gate"]["f1"] = bad
    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", lambda **kwargs: diagnostic)
    result = pipeline.run_audit(
        question="Q", response=ANSWER, reference=None, question_meta=META,
        session_id=None, semantic_judge=judge(("fulfilled", "fulfilled")),
    )
    assert result["verdict"] == "accept"
    # Starlette and compliant clients reject non-finite JSON numbers. An optional
    # diagnostic failure must not turn a grounded successful audit into HTTP 500.
    json.dumps(result, allow_nan=False)


def test_review_diagnostic_execution_cannot_change_saved_requirement_context(monkeypatch):
    # A legacy helper must not be allowed to alter the canonical requirement list
    # between semantic assessment and final provenance validation.
    original = copy.deepcopy(META)
    def mutating_diagnostic(**kwargs):
        kwargs["question_meta"]["core_propositions"].append("Injected diagnostic requirement")
        return legacy()
    monkeypatch.setattr(pipeline, "_run_legacy_diagnostics", mutating_diagnostic)
    result = pipeline.run_audit(
        question="Q", response=ANSWER, reference=None, question_meta=original,
        session_id=None, semantic_judge=judge(("fulfilled", "fulfilled")),
    )
    assert result["verdict"] == "accept"
    assert original == META
    assert result["_question_meta"] == META


@pytest.mark.parametrize("target", ["coverage", "structural"])
def test_review_extreme_json_integer_cannot_crash_remote_boundary(target):
    assessment = assess_requirements(META, ANSWER, judge(("fulfilled", "fulfilled")))
    frame = apply_semantic_gate(legacy(), assessment, response=ANSWER, question_meta=META)
    # A valid JSON integer below Python's default 4300-digit parse bound, but
    # larger than float conversion can represent. Validate ranges before isfinite.
    if target == "coverage":
        frame["C"] = 10 ** 3999
    else:
        frame["structural_gate"]["f1"] = 10 ** 3999
    assert mcp_server._valid_remote_metrics(frame) is False


def test_review_python_optional_recalculation_error_preserves_semantic_result(monkeypatch):
    monkeypatch.setattr(python_audit, "detect", lambda *args: Evidence(question_id="q"))
    def broken(*args):
        raise ArithmeticError("Optional legacy calculation failed")
    monkeypatch.setattr(python_audit, "calculate", broken)
    result = python_audit.audit("q", ANSWER, META,
                                semantic_judge=judge(("fulfilled", "fulfilled")))
    assert result["verdict"] == "accept" and result["state"]["C"] == 1.0
    assert result["state"]["S"] is result["state"]["delta_e"] is None
    assert "python_legacy_state_error:ArithmeticError" in result["legacy_metrics"]["errors"]
    json.dumps(result, allow_nan=False)


def test_review_python_nonfinite_evidence_cannot_escape_json_guard(monkeypatch):
    monkeypatch.setattr(python_audit, "detect", lambda *args: Evidence(
        question_id="q", f1_anchor=float("nan"), f3_operator=float("inf")))
    result = python_audit.audit("q", ANSWER, META,
                                semantic_judge=judge(("fulfilled", "fulfilled")))
    assert result["verdict"] == "accept" and result["state"]["C"] == 1.0
    assert result["evidence"]["f1_anchor"] is result["evidence"]["f3_operator"] is None
    json.dumps(result, allow_nan=False)


def test_review_cli_schema4_known_metadata_uses_shared_semantic_authority(monkeypatch, tmp_path, capsys):
    data = tmp_path / "metadata.jsonl"
    data.write_text(json.dumps({"id": "q", **META}) + "\n", encoding="utf-8")
    monkeypatch.setattr(python_audit, "make_judge_from_env",
                        lambda: judge(("fulfilled", "fulfilled")))
    monkeypatch.setattr(python_audit, "detect", lambda *args: Evidence(
        question_id="q", f1_anchor=1.0, f3_operator=1.0))
    monkeypatch.setattr(sys, "argv", ["audit.py", "--question-id", "q", "--data",
                                      str(data), "--response", ANSWER])
    python_audit.main()
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "4.0.0" and result["verdict"] == "accept"
    assert result["decision_scope"] == "instruction_coverage_v2"
    assert result["state"]["C"] == 1.0
    assert result["state"]["S"] is result["state"]["delta_e"] is None
