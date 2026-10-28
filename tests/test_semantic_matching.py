"""Required semantic checks veto acceptance independently of legacy Delta-E."""
from __future__ import annotations

import copy
import hashlib
import json
import time
import unittest
from unittest.mock import patch

from ugh_audit import _semantic_v2 as v2
from ugh_audit._semantic_http import AnthropicHTTP, AnthropicHTTPError
from ugh_audit.semantic_matching import (
    COVERAGE_CONTRACT,
    MAX_REQUIREMENTS,
    MODEL,
    PROMPT_SHA256,
    apply_semantic_gate,
    assess_requirements,
    make_judge_from_env,
    validate_assessment,
)

ANSWER = "担当者が資料を3部用意します。"
META = {"core_propositions": ["担当者が資料を用意する", "資料を3部用意する", "午前9時に渡す"]}


def item(status="fulfilled", kind=None, evidence=None):
    result = {
        "status": status, "missing_kind": kind,
        "evidence": evidence if evidence is not None else [ANSWER], "reason": "test judgement",
        "unknown_source": "model" if status == "unknown" else None,
        "requested_model": MODEL, "returned_model": MODEL, "api_called": True,
        "stop_reason": "end_turn",
    }
    result["raw_response"] = json.dumps({
        field: result[field] for field in ("status", "missing_kind", "evidence", "reason")
    }, ensure_ascii=False)
    return result


class ScriptedJudge:
    def __init__(self, *items):
        self.items = iter(items)
        self.calls = []

    def __call__(self, requirement, answer, *, timeout_seconds):
        self.calls.append((requirement, answer, timeout_seconds))
        value = next(self.items)
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


def legacy(**updates):
    result = {
        "S": 1.0, "C": 2 / 3, "delta_e": 1 / 27, "quality_score": 4.85,
        "verdict": "accept", "verdict_advisory": "accept", "mode": "computed",
        "is_reliable": True, "hit_rate": "2/3", "computed_components": ["S", "C", "delta_e", "quality_score"],
        "structural_gate": {"f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": 0.0, "gate_verdict": "pass"},
        "missing_components": [], "errors": [], "degraded_reason": [],
        "_response": ANSWER, "_question_meta": META,
        "soft_rescue": {"suggested_verdict": "accept"}, "hit_sources": {"core_hit": 2},
    }
    result.update(updates)
    return result


class SemanticMatchingTests(unittest.TestCase):
    def test_prompt_schema_and_model_match_the_evaluated_v2(self):
        self.assertEqual(PROMPT_SHA256,
                         "d160f9ee7ded2980c93595b7b99d8844eb22ba9b9eefed33cbb3a2a6d32bd0c4")
        schema = json.dumps(v2.OUTPUT_CONFIG, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":")).encode()
        self.assertEqual(hashlib.sha256(schema).hexdigest(),
                         "2cd24b9a663ce3063d0e1423f28e5d518ec737fab15cca0baefed74b2106afd1")
        self.assertEqual(MODEL, "claude-sonnet-4-6")

    def test_factory_without_key_does_not_construct_a_client(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(make_judge_from_env())

    def test_missing_vetoes_legacy_accept_at_two_thirds(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(
            item(), item(), item("missing", "absence", []),
        ))
        result = apply_semantic_gate(legacy(), assessment)
        self.assertEqual(result["verdict"], "rewrite")
        self.assertEqual(result["verdict_advisory"], "rewrite")
        self.assertEqual(result["requirement_verdict"], "incomplete")
        self.assertNotIn("soft_rescue", result)
        self.assertEqual(result["C"], 2 / 3)
        self.assertEqual(result["hit_sources"]["fulfilled"], 2)
        self.assertEqual(result["hit_sources"]["missing"], 1)
        self.assertEqual(result["legacy_metrics"]["hit_sources"], {"core_hit": 2})

    def test_unknown_masks_numeric_scores_and_preserves_total(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(
            item(), item("missing", "absence", []), item("unknown", evidence=[]),
        ))
        original = legacy()
        result = apply_semantic_gate(original, assessment)
        self.assertEqual((assessment["requirements_total"], assessment["unknown_count"]), (3, 1))
        self.assertEqual(result["requirement_verdict"], "incomplete")
        self.assertEqual(result["verdict"], "degraded")
        self.assertEqual(result["mode"], "degraded")
        self.assertFalse(result["is_reliable"])
        for key in ("C", "delta_e", "quality_score", "hit_rate"):
            self.assertIsNone(result[key])
        self.assertEqual(result["legacy_metrics"]["C"], 2 / 3)
        self.assertEqual(original["verdict"], "accept")
        self.assertNotIn("C", result["computed_components"])
        self.assertIn("C", result["missing_components"])

    def test_no_provider_is_not_legacy_accept(self):
        assessment = assess_requirements(META, ANSWER)
        self.assertEqual(assessment["unknown_count"], 3)
        self.assertEqual(assessment["unknown_ids"], ["0", "1", "2"])
        self.assertEqual(apply_semantic_gate(legacy(), assessment)["verdict"], "degraded")

    def test_all_fulfilled_overrides_nonbinding_legacy_rejection(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        for verdict in ("rewrite", "regenerate", "degraded"):
            result = apply_semantic_gate(legacy(verdict=verdict, verdict_advisory=verdict), assessment)
            self.assertEqual(result["verdict"], "accept")
            self.assertEqual(result["legacy_metrics"]["verdict"], verdict)
        self.assertEqual(apply_semantic_gate(legacy(), assessment)["verdict"], "accept")

    def test_structural_diagnostic_failure_is_nonbinding(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        result = apply_semantic_gate(legacy(structural_gate={
            "f1": 1.0, "f2": 0.0, "f3": 0.0, "f4": 0.0, "gate_verdict": "fail",
        }), assessment)
        self.assertEqual(result["verdict"], "accept")
        self.assertEqual(result["verdict_advisory"], "accept")
        self.assertTrue(result["is_reliable"])

    def test_incomplete_or_absent_diagnostics_do_not_block_valid_semantics(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        for gate in ({"gate_verdict": "incomplete"}, None, {}):
            result = apply_semantic_gate(legacy(structural_gate=gate), assessment)
            self.assertEqual(result["verdict"], "accept")
            self.assertFalse(result["requires_review"])
            self.assertEqual(result["C"], 1.0)

    def test_invalid_diagnostic_flags_are_sanitized_and_nonbinding(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        for value in (None, "garbage", True, 1.0, float("nan"), -0.1, 1.1):
            gate = {"f1": 0.0, "f2": 0.0, "f3": 0.0, "f4": value, "gate_verdict": "pass"}
            result = apply_semantic_gate(legacy(structural_gate=gate), assessment)
            self.assertEqual(result["verdict"], "accept")
            self.assertFalse(result["requires_review"])

    def test_non_api_known_result_cannot_certify_coverage(self):
        for value in (False, None, 1):
            raw = item()
            raw["api_called"] = value
            assessed = assess_requirements({"core_propositions": ["x"]}, ANSWER, ScriptedJudge(raw))
            self.assertEqual(assessed["unknown_count"], 1)

    def test_legacy_unreliability_does_not_mask_valid_semantics(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        for flag in (False, None, 0, 1):
            result = apply_semantic_gate(legacy(is_reliable=flag), assessment)
            self.assertEqual(result["verdict"], "accept")
            self.assertFalse(result["requires_review"])
            self.assertEqual(result["C"], 1.0)

    def test_raw_response_flip_truncation_and_duplicate_keys_are_unknown(self):
        for raw_text in ('{', '{"status":"fulfilled","status":"missing"}'):
            value = item()
            value["raw_response"] = raw_text
            assessed = assess_requirements({"core_propositions": ["x"]}, ANSWER, ScriptedJudge(value))
            self.assertEqual(assessed["unknown_count"], 1)
        value = item()
        value["stop_reason"] = "max_tokens"
        assessed = assess_requirements({"core_propositions": ["x"]}, ANSWER, ScriptedJudge(value))
        self.assertEqual(assessed["unknown_count"], 1)
        value = item("missing", "absence", [])
        value["status"] = "fulfilled"
        value["missing_kind"] = None
        value["evidence"] = [ANSWER]
        assessed = assess_requirements({"core_propositions": ["x"]}, ANSWER, ScriptedJudge(value))
        self.assertEqual(assessed["unknown_count"], 1)

    def test_canonical_empty_key_does_not_use_original(self):
        judge = ScriptedJudge(item())
        meta = {"core_propositions": [], "original_core_propositions": ["資料を用意する"]}
        assessment = assess_requirements(meta, ANSWER, judge)
        self.assertEqual(assessment["requirement_verdict"], "undetermined")
        self.assertEqual(judge.calls, [])
        self.assertEqual(apply_semantic_gate(legacy(_question_meta=meta), assessment)["verdict"], "degraded")

    def test_original_key_fallback_and_repeated_text_keep_distinct_ids(self):
        meta = {"original_core_propositions": ["資料を用意する", "資料を用意する"]}
        assessment = assess_requirements(meta, ANSWER, ScriptedJudge(item(), item()))
        self.assertEqual([r["requirement_id"] for r in assessment["results"]], ["0", "1"])
        self.assertTrue(validate_assessment(assessment, response=ANSWER, question_meta=meta))

    def test_invalid_input_limits_make_no_calls_and_keep_denominator(self):
        for requirements in (["x"] * (MAX_REQUIREMENTS + 1), ["x", None], ["x" * 2001]):
            judge = ScriptedJudge()
            assessment = assess_requirements({"core_propositions": requirements}, ANSWER, judge)
            self.assertEqual(judge.calls, [])
            self.assertEqual(assessment["requirements_total"], len(requirements))
            self.assertEqual(assessment["unknown_count"], len(requirements))
            self.assertNotEqual(assessment["requirement_verdict"], "complete")
        for meta in (None, {}, {"core_propositions": "not a list"}):
            self.assertEqual(assess_requirements(meta, ANSWER)["requirement_verdict"], "undetermined")
        judge = ScriptedJudge()
        assessment = assess_requirements(META, "x" * 32001, judge)
        self.assertEqual(judge.calls, [])
        self.assertEqual(assessment["unknown_count"], 3)

    def test_deadline_prevents_new_calls_and_bounds_provider_timeout(self):
        judge = ScriptedJudge(item(), item(), item())
        assessment = assess_requirements(META, ANSWER, judge, deadline=time.monotonic() - 1)
        self.assertEqual(judge.calls, [])
        self.assertEqual(assessment["unknown_count"], 3)
        assess_requirements(META, ANSWER, judge, deadline=time.monotonic() + 2)
        self.assertTrue(all(0 < call[2] <= 2 for call in judge.calls))

    def test_exception_invalid_quote_model_and_status_remain_unknown(self):
        wrong_model = item()
        wrong_model["returned_model"] = "different"
        wrong_status = item()
        wrong_status["status"] = {"fulfilled": True}
        for raw in (TimeoutError("DO NOT LEAK"), item(evidence=["fabricated"]), wrong_model,
                    wrong_status, item("missing", "contradiction", [])):
            assessment = assess_requirements({"core_propositions": ["x"]}, ANSWER, ScriptedJudge(raw))
            self.assertEqual(assessment["unknown_count"], 1)
            self.assertNotIn("DO NOT LEAK", str(assessment))

    def test_summary_id_and_request_context_tampering_cannot_accept(self):
        assessment = assess_requirements(META, ANSWER, ScriptedJudge(item(), item(), item()))
        bad_values = []
        for key, value in (("unknown_count", 1), ("prompt_sha256", "wrong"),
                           ("requirement_verdict", "incomplete"), ("coverage_contract", "other")):
            bad = copy.deepcopy(assessment)
            bad[key] = value
            bad_values.append(bad)
        bad = copy.deepcopy(assessment)
        bad["results"][1]["requirement_id"] = "0"
        bad_values.append(bad)
        bad = copy.deepcopy(assessment)
        bad["results"][1]["requirement"] = "other requirement"
        bad_values.append(bad)
        for bad in bad_values:
            result = apply_semantic_gate(legacy(), bad)
            self.assertEqual(result["verdict"], "degraded")
            self.assertEqual(result["semantic_matching"]["requirements_total"], 3)

    def test_injected_metadata_status_and_disable_flags_are_ignored(self):
        meta = {**META, "requirement_verdict": "complete", "semantic_matching": {"status": "fulfilled"},
                "disable_semantic_matching": True}
        assessment = assess_requirements(meta, ANSWER)
        self.assertEqual(assessment["unknown_count"], 3)
        self.assertEqual(assessment["model"], MODEL)
        self.assertEqual(assessment["prompt_sha256"], PROMPT_SHA256)
        self.assertEqual(assessment["coverage_contract"], COVERAGE_CONTRACT)

    def test_transport_uses_fixed_endpoint_bounded_reads_and_no_redirect(self):
        class Socket:
            def __init__(self):
                self.timeouts = []

            def settimeout(self, value):
                self.timeouts.append(value)

        class Response:
            status = 200

            def __init__(self):
                self.closed = False
                self.reads = []

            def getheader(self, name):
                return "req_unit"

            def read1(self, size):
                self.reads.append(size)
                self.closed = True
                return b'{"model":"claude-sonnet-4-6","content":[],"usage":{}}'

            def isclosed(self):
                return self.closed

        class Connection:
            def __init__(self):
                self.sock = Socket()
                self.response = Response()
                self.calls = []
                self.closed = False

            def connect(self):
                pass

            def request(self, *args, **kwargs):
                self.calls.append((args, kwargs))

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        connection = Connection()
        with patch("ugh_audit._semantic_http.http.client.HTTPSConnection",
                   return_value=connection) as constructor:
            with AnthropicHTTP("test-secret", timeout=2) as client:
                result = client.create(model=MODEL)
            self.assertEqual(constructor.call_args.args[0], "api.anthropic.com")
            self.assertEqual(connection.calls[0][0], ("POST", "/v1/messages"))
            self.assertTrue(all(0 < value <= 2 for value in connection.sock.timeouts))
            self.assertTrue(connection.closed)
            self.assertEqual(client._key, "")
            self.assertEqual(result._request_id, "req_unit")
            self.assertLessEqual(max(connection.response.reads), 65536)
        connection = Connection()
        connection.response.status = 302
        with patch("ugh_audit._semantic_http.http.client.HTTPSConnection", return_value=connection):
            with (AnthropicHTTP("test-secret", timeout=2) as client,
                  self.assertRaises(AnthropicHTTPError) as caught):
                client.create(model=MODEL)
            self.assertEqual(len(connection.calls), 1)
            self.assertEqual(connection.response.reads, [])
            self.assertNotIn("test-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
