"""Required v2 instruction coverage and a downgrade-only acceptance guard.

Legacy C/Delta-E remain deterministic diagnostics. They cannot authorize accept
without a complete semantic assessment. No caller-supplied metadata verdict is
read; the only metadata input is the canonical requirement list.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections.abc import Callable

from . import _semantic_v2 as v2
from ._semantic_http import AnthropicHTTP

MODEL = v2.MODEL
COVERAGE_CONTRACT = v2.COVERAGE_CONTRACT
PROMPT_SHA256 = hashlib.sha256(v2.SYSTEM.encode("utf-8")).hexdigest()
MAX_REQUIREMENTS = 16
MAX_ANSWER_CHARS = 32000
MAX_REQUIREMENT_CHARS = 2000
MATCHING_TIMEOUT_SECONDS = 25.0
PER_REQUIREMENT_TIMEOUT_SECONDS = 10.0
_STATUSES = ("fulfilled", "missing", "unknown")
_VERDICTS = ("accept", "rewrite", "regenerate", "degraded")


class SemanticJudge:
    """Server-owned provider; credentials are never read from request metadata."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    def __call__(self, requirement: str, answer: str, *, timeout_seconds: float) -> dict:
        with AnthropicHTTP(self._api_key, timeout=timeout_seconds) as client:
            return v2.judge(client, requirement, answer)


def make_judge_from_env() -> SemanticJudge | None:
    value = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    return SemanticJudge(value) if value else None


def _requirements(question_meta) -> list | None:
    if not isinstance(question_meta, dict):
        return None
    value = (question_meta["core_propositions"] if "core_propositions" in question_meta
             else question_meta.get("original_core_propositions"))
    return value if isinstance(value, list) else None


def _unknown(reason: str, **metadata) -> dict:
    return {"status": "unknown", "missing_kind": None, "evidence": [],
            "unknown_source": "system", "reason": reason, "api_called": False, **metadata}


def _valid_item(item, response: str) -> bool:
    if not isinstance(item, dict) or item.get("status") not in _STATUSES:
        return False
    status, kind = item["status"], item.get("missing_kind")
    evidence, reason = item.get("evidence"), item.get("reason")
    if (not isinstance(reason, str) or not reason.strip()
            or not isinstance(evidence, list)
            or any(not isinstance(q, str) or not q.strip() or q not in response for q in evidence)):
        return False
    if status == "missing":
        if kind not in ("absence", "contradiction"):
            return False
    elif kind is not None:
        return False
    if (status == "fulfilled" or kind == "contradiction") and not evidence:
        return False
    if status == "unknown":
        if item.get("unknown_source") not in ("model", "system"):
            return False
        if item["unknown_source"] == "system":
            return True
    elif item.get("unknown_source") is not None:
        return False
    if item.get("api_called") is not True or item.get("stop_reason") != "end_turn":
        return False
    if item.get("requested_model") != MODEL or item.get("returned_model") != MODEL:
        return False
    try:
        raw = json.loads(item.get("raw_response"), object_pairs_hook=v2._unique_object,
                         parse_constant=v2._reject_constant)
    except (ValueError, TypeError, RecursionError):
        return False
    fields = ("status", "missing_kind", "evidence", "reason")
    return (isinstance(raw, dict) and set(raw) == set(fields)
            and all(raw[field] == item.get(field) for field in fields))


def _summarize(rows: list[dict], *, reason: str | None = None) -> dict:
    total = len(rows)
    missing = [row["requirement_id"] for row in rows if row["status"] == "missing"]
    unknown = [row["requirement_id"] for row in rows if row["status"] == "unknown"]
    fulfilled = sum(row["status"] == "fulfilled" for row in rows)
    verdict = "incomplete" if missing else "undetermined" if unknown or not total else "complete"
    result = {
        "requirement_verdict": verdict, "requires_review": verdict != "complete",
        "requirements_total": total, "fulfilled_count": fulfilled,
        "missing_count": len(missing), "unknown_count": len(unknown),
        "missing_ids": missing, "unknown_ids": unknown, "results": rows,
        "coverage": fulfilled / total if total and not unknown else None,
        "model": MODEL, "prompt_sha256": PROMPT_SHA256, "coverage_contract": COVERAGE_CONTRACT,
    }
    if reason:
        result["reason"] = reason
    return result


def _validated_structural_gate(gate) -> str | None:
    """A remote label cannot certify missing, invalid, or contradictory flags."""
    if not isinstance(gate, dict):
        return None
    values = [gate.get(key) for key in ("f1", "f2", "f3", "f4")]
    if any(type(value) not in (int, float) or not math.isfinite(value)
           or not 0 <= value <= 1 for value in values):
        return None
    maximum = max(values)
    derived = "fail" if maximum >= 1 else "warn" if maximum > 0 else "pass"
    return derived if gate.get("gate_verdict") == derived else None


def assess_requirements(
    question_meta, response, judge_fn: Callable | None = None, *, deadline: float | None = None,
) -> dict:
    """Assess each indexed requirement once; unresolved rows remain in the denominator."""
    requirements = _requirements(question_meta)
    if requirements is None or not requirements:
        return _summarize([], reason="requirements_invalid_or_missing")
    blocked = None
    if len(requirements) > MAX_REQUIREMENTS:
        blocked = "requirement_limit"
    elif any(not isinstance(req, str) or not req.strip()
             or len(req) > MAX_REQUIREMENT_CHARS for req in requirements):
        blocked = "requirements_invalid"
    elif not isinstance(response, str) or not response.strip() or len(response) > MAX_ANSWER_CHARS:
        blocked = "answer_invalid_or_too_large"
    elif judge_fn is None:
        blocked = "semantic_checker_unavailable"
    if deadline is None:
        deadline = time.monotonic() + MATCHING_TIMEOUT_SECONDS
    if type(deadline) not in (int, float) or not math.isfinite(deadline):
        blocked = "deadline_invalid"
    rows = []
    for index, requirement in enumerate(requirements):
        if blocked:
            item = _unknown(blocked)
        elif deadline <= time.monotonic():
            item = _unknown("deadline_exceeded")
        else:
            try:
                raw = judge_fn(requirement, response, timeout_seconds=min(
                    PER_REQUIREMENT_TIMEOUT_SECONDS, deadline - time.monotonic(),
                ))
                if time.monotonic() > deadline:
                    item = _unknown("deadline_exceeded", api_called=True)
                elif (not _valid_item(raw, response)
                      or ("requirement_id" in raw and raw["requirement_id"] != str(index))):
                    item = _unknown("invalid_semantic_result", api_called=True)
                else:
                    allowed = ("status", "missing_kind", "evidence", "reason", "unknown_source",
                               "api_called", "elapsed_ms", "requested_model", "returned_model",
                               "stop_reason", "usage", "request_id", "raw_response", "http_status",
                               "error_type", "rate_limits")
                    item = {key: raw[key] for key in allowed if key in raw}
            except Exception as exc:  # noqa: BLE001 -- an injected/provider failure cannot accept
                item = _unknown("semantic_checker_error", api_called=True,
                                error_type=type(exc).__name__)
        rows.append({**item, "requirement_id": str(index), "requirement": requirement})
    return _summarize(rows, reason=blocked)


def validate_assessment(assessment, *, response=None, question_meta=None) -> bool:
    """Reconcile summaries and rows, plus request context when available (e.g. proxy)."""
    if not isinstance(assessment, dict):
        return False
    if (assessment.get("model") != MODEL or assessment.get("prompt_sha256") != PROMPT_SHA256
            or assessment.get("coverage_contract") != COVERAGE_CONTRACT):
        return False
    rows = assessment.get("results")
    total = assessment.get("requirements_total")
    if not isinstance(rows, list) or type(total) is not int or total != len(rows):
        return False
    if any(type(assessment.get(key)) is not int for key in (
        "fulfilled_count", "missing_count", "unknown_count",
    )) or type(assessment.get("requires_review")) is not bool:
        return False
    expected = _requirements(question_meta) if question_meta is not None else None
    if question_meta is not None and len(expected or []) != total:
        return False
    if response is not None and not isinstance(response, str):
        return False
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("requirement_id") != str(index):
            return False
        if expected is not None and row.get("requirement") != expected[index]:
            return False
        if response is not None and not _valid_item(row, response):
            return False
        if response is None and row.get("status") not in _STATUSES:
            return False
        if (total > MAX_REQUIREMENTS or not isinstance(row.get("requirement"), str)
                or not row["requirement"].strip()
                or len(row["requirement"]) > MAX_REQUIREMENT_CHARS) and row.get("status") != "unknown":
            return False
    calculated = _summarize(rows)
    for key in ("requirement_verdict", "requires_review", "requirements_total", "fulfilled_count",
                "missing_count", "unknown_count", "missing_ids", "unknown_ids", "coverage"):
        if assessment.get(key) != calculated[key]:
            return False
    return True


def apply_semantic_gate(result: dict, assessment, *, response=None, question_meta=None) -> dict:
    """Return a guarded copy; no completeness failure can authorize acceptance."""
    guarded = dict(result)
    response = response if response is not None else result.get("_response")
    question_meta = question_meta if question_meta is not None else result.get("_question_meta")
    if response is None or not validate_assessment(
        assessment, response=response, question_meta=question_meta,
    ):
        requirements = _requirements(question_meta)
        if requirements is None and isinstance(assessment, dict):
            rows = assessment.get("results")
            if isinstance(rows, list):
                requirements = [row.get("requirement") if isinstance(row, dict) else None
                                for row in rows]
        assessment = _summarize([
            {**_unknown("invalid_semantic_assessment"), "requirement_id": str(index),
             "requirement": req} for index, req in enumerate(requirements or [])
        ], reason="invalid_semantic_assessment")
    if not isinstance(guarded.get("legacy_metrics"), dict):
        guarded["legacy_metrics"] = {key: result.get(key) for key in (
            "S", "C", "delta_e", "quality_score", "verdict", "mode", "hit_rate",
        )}
    guarded.update(semantic_matching=assessment,
                   requirement_verdict=assessment["requirement_verdict"],
                   requires_review=assessment["requires_review"])
    unknown = bool(assessment["unknown_count"] or not assessment["requirements_total"])
    structural = result.get("structural_gate")
    structural_state = _validated_structural_gate(structural)
    structural_unknown = structural_state is None
    legacy_degraded = result.get("verdict") == "degraded" or result.get("mode") == "degraded"
    unreliable_accept = result.get("verdict") == "accept" and result.get("is_reliable") is not True
    blocked_reason = ("semantic_matching_undetermined" if unknown
                      else "structural_gate_undetermined" if structural_unknown
                      else "legacy_audit_undetermined" if legacy_degraded
                      else "audit_result_unreliable" if unreliable_accept
                      else "audit_result_invalid")
    def strings(value):
        return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []

    errors = strings(result.get("errors"))
    if (unknown or structural_unknown or legacy_degraded or unreliable_accept
            or result.get("verdict") not in _VERDICTS
            or result.get("mode") not in ("computed", "computed_ai_draft", "degraded")):
        guarded.update(verdict="degraded", mode="degraded", is_reliable=False,
                       C=None, delta_e=None, quality_score=None, hit_rate=None)
        fields = ("C", "delta_e", "quality_score")
        guarded["computed_components"] = [value for value in strings(result.get("computed_components"))
                                           if value not in fields]
        guarded["missing_components"] = sorted(set(strings(result.get("missing_components"))) | set(fields))
        if blocked_reason not in errors:
            errors.append(blocked_reason)
        guarded["degraded_reason"] = list(dict.fromkeys(
            strings(result.get("degraded_reason")) + [blocked_reason],
        ))
    elif assessment["missing_count"] and result["verdict"] == "accept":
        guarded["verdict"] = "rewrite"
        errors.append("requirements_missing")
    if (isinstance(structural, dict) and structural.get("gate_verdict") == "fail"
            and guarded["verdict"] == "accept"):
        guarded.update(verdict="regenerate", is_reliable=False)
        errors.append("structural_gate_failed")
    if guarded.get("mode") == "degraded":
        guarded.update(verdict="degraded", is_reliable=False)
    if guarded.get("verdict_advisory") not in _VERDICTS:
        guarded["verdict_advisory"] = guarded["verdict"]
    if guarded["verdict"] == "degraded":
        guarded["verdict_advisory"] = "degraded"
    elif (guarded["verdict"] != "accept"
          and guarded.get("verdict_advisory", "accept") == "accept"):
        guarded["verdict_advisory"] = guarded["verdict"]
    if assessment["requirement_verdict"] != "complete":
        guarded.pop("soft_rescue", None)
    guarded["requires_review"] = assessment["requires_review"] or guarded["verdict"] != "accept"
    guarded["errors"] = list(dict.fromkeys(errors))
    return guarded
