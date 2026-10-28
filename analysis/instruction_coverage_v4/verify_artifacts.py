"""Recount frozen synthetic evidence offline with Python 3.10+ and the standard library."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_value):
    raise ValueError("non-finite JSON constant")


def parse(text):
    return json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)


def load(path):
    return parse(path.read_text(encoding="utf-8-sig"))


def rows(path):
    return [parse(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def decide(statuses):
    if not statuses or "unknown" in statuses:
        return "degraded"
    return "rewrite" if "missing" in statuses else "accept"


class Audit:
    def __init__(self, root):
        self.root = root.resolve()
        self.errors = []
        self.checks = 0
        self.manifest = load(self.root / "manifest.json")

    def check(self, condition, code):
        self.checks += 1
        if not condition:
            self.errors.append(code)

    def file(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("artifact path escapes root")
        return path

    def original_sha(self, name):
        entry = self.manifest["files"][name]
        return entry.get("original_sha256", entry["sha256"])

    def verify_manifest(self):
        for name, entry in self.manifest["files"].items():
            path = self.file(name)
            self.check(path.is_file(), "missing_file:" + name)
            if path.is_file():
                self.check(sha(path) == entry["sha256"], "file_hash:" + name)
                self.check(path.stat().st_size == entry["bytes"], "file_size:" + name)
            if entry.get("transform"):
                transform = entry["transform"]
                self.check(transform.get("type") == "relative_path_fields" and
                           transform.get("fields") in (["input_file", "output_file"],
                                                        ["input", "output"]),
                           "unrecognized_transform:" + name)
        self.check(self.manifest["evaluated_source_commit"] ==
                   "3e0a2897ccd1dd2e16b90032d0cec70c299251a2", "evaluated_commit")
        self.check(self.manifest["human_validation"] is False, "synthetic_scope")

    def gold(self, name):
        result = {}
        for question in rows(self.file(name)):
            for answer in question["answers"]:
                key = question["qid"] + "." + answer["variant"]
                self.check(key not in result, "duplicate_gold:" + key)
                result[key] = {
                    "question": question["question"],
                    "requirements": question["requirements"],
                    "answer": answer["answer"],
                    "labels": answer.get("expected_statuses", answer.get("labels")),
                    "missing_kinds": answer["missing_kinds"],
                }
        return result

    def verify_dataset(self, fresh):
        label = "fresh80" if fresh else "known256"
        path = "evidence/fresh/live.jsonl" if fresh else "evidence/replay-frozen.jsonl"
        gold_path = ("evidence/fresh/gold-final.jsonl" if fresh else
                     "evidence/prior/gold_questions.jsonl")
        summary_path = "summaries/fresh-summary.json" if fresh else "summaries/replay-summary.json"
        records, gold = rows(self.file(path)), self.gold(gold_path)
        summary = load(self.file(summary_path))
        old = {} if fresh else {r["id"]: r for r in rows(
            self.file("evidence/prior/scored.jsonl"))}
        expected_answers, expected_requirements = (80, 280) if fresh else (256, 896)
        ids = [r["id"] for r in records]
        self.check(len(ids) == len(set(ids)) == len(gold) == expected_answers,
                   label + ":unique_cases")
        self.check(set(ids) == set(gold), label + ":gold_ids")
        if not fresh:
            self.check(set(ids) == set(old), label + ":old_ids")
        confusion, kinds, actual, unknowns = Counter(), Counter(), Counter(), Counter()
        arms = {name: {"false_accepts": 0, "false_rejects": 0, "exact_correct": 0,
                       "admission_correct": 0, "verdict_counts": Counter()}
                for name in (("per_requirement_B", "repaired_UGH") if fresh else
                             ("old_UGH", "per_requirement_B", "repaired_UGH"))}
        requests, requirements, missing_correct, raw_count = [], 0, 0, 0
        for record in records:
            key, out, item = record["id"], record["output"], gold[record["id"]]
            for field in ("question", "requirements", "answer"):
                self.check(record[field] == item[field], label + ":context:" + key)
                if not fresh:
                    self.check(record[field] == old[key][field], label + ":old_context:" + key)
            self.check(record["gold_statuses"] == item["labels"] and
                       record["gold_missing_kinds"] == item["missing_kinds"],
                       label + ":gold_labels:" + key)
            proof = out["semantic_matching"]
            checked = proof["results"]
            statuses = [c["status"] for c in checked]
            counts = Counter(statuses)
            self.check(len(checked) == len(item["labels"]) == len(item["requirements"]),
                       label + ":denominator:" + key)
            self.check(statuses == record["semantic_statuses"], label + ":statuses:" + key)
            self.check(decide(statuses) == out["verdict"] == record["B_verdict"] ==
                       record["new_verdict"], label + ":decision:" + key)
            self.check(out["schema_version"] == "4.0.0" and
                       out["decision_scope"] == "instruction_coverage_v2" and
                       out["metrics_scope"] == "semantic_coverage_only" and
                       out["structural_gate"]["binding"] is False,
                       label + ":contract:" + key)
            self.check(all(out[k] is None for k in ("S", "delta_e", "quality_score")),
                       label + ":unsupported_metrics:" + key)
            coverage = None if counts["unknown"] else counts["fulfilled"] / len(checked)
            hit_rate = None if counts["unknown"] else f"{counts['fulfilled']}/{len(checked)}"
            self.check(out["C"] == coverage and out["hit_rate"] == hit_rate and
                       proof["coverage"] == coverage,
                       label + ":coverage:" + key)
            self.check(proof["requirements_total"] == len(checked) and
                       all(proof[k + "_count"] == counts[k] for k in
                           ("fulfilled", "missing", "unknown")), label + ":counts:" + key)
            self.check(out["assessment_context"]["core_propositions"] == item["requirements"],
                       label + ":assessment_context:" + key)
            self.check(proof["model"] == self.freeze["model"] and
                       proof["prompt_sha256"] == self.freeze["prompt_sha256"] and
                       proof["coverage_contract"] == self.freeze["decision_scope"],
                       label + ":model_contract:" + key)
            if fresh:
                self.check(record["http_status"] == 200 and not record["harness_error"],
                           label + ":transport:" + key)
                self.check(stamp(record["started_at_utc"]) >= stamp(self.protocol["created_at_utc"]),
                           label + ":request_chronology:" + key)
            else:
                self.check(record["old_verdict"] == old[key]["ugh"]["verdict"],
                           label + ":old_verdict:" + key)
            for index, check in enumerate(checked):
                raw = parse(check["raw_response"])
                self.check(set(raw) == {"status", "missing_kind", "evidence", "reason"} and
                           all(raw[k] == check[k] for k in raw), label + ":raw_projection:" + key)
                self.check(check["requirement_id"] == str(index) and
                           check["requirement"] == item["requirements"][index],
                           label + ":requirement_index:" + key)
                self.check(check["api_called"] is True and check["requested_model"] ==
                           check["returned_model"] == self.freeze["model"] and
                           check["stop_reason"] == "end_turn", label + ":provider_metadata:" + key)
                self.check(all(isinstance(q, str) and q.strip() and q in item["answer"]
                               for q in check["evidence"]) and
                           (bool(check["evidence"]) or
                            (check["status"] != "fulfilled" and
                             check["missing_kind"] != "contradiction")), label + ":quote:" + key)
                if not fresh:
                    original = old[key]["per_requirement"]["checks"][index]
                    self.check(all(check.get(k) == v for k, v in original.items()
                                   if k != "request_payload_sha256"),
                               label + ":recorded_proof_identity:" + key)
                requests.append(check["request_id"])
                requirements += 1
                raw_count += 1
                actual[check["status"]] += 1
                confusion[item["labels"][index] + "->" + check["status"]] += 1
                if check["status"] == "unknown":
                    unknowns[check.get("unknown_source", "unspecified")] += 1
                if item["labels"][index] == "missing":
                    kinds[item["missing_kinds"][index]] += 1
                    missing_correct += check["missing_kind"] == item["missing_kinds"][index]
            expected = decide(item["labels"])
            for name, arm in arms.items():
                verdict = record["old_verdict"] if name == "old_UGH" else out["verdict"]
                arm["false_accepts"] += expected != "accept" and verdict == "accept"
                arm["false_rejects"] += expected == "accept" and verdict != "accept"
                arm["exact_correct"] += expected == verdict
                arm["admission_correct"] += (expected == "accept") == (verdict == "accept")
                arm["verdict_counts"][verdict] += 1
        self.check(requirements == len(set(requests)) == expected_requirements,
                   label + ":unique_requirement_calls")
        self.check(sha(self.file(path)) == summary["output_sha256"], label + ":summary_input_hash")
        self.check(dict(confusion) == summary["requirement_status_confusion"] and
                   missing_correct == summary["missing_kind_correct"], label + ":summary_metrics")
        for name, arm in arms.items():
            expected = summary["arms"][name]
            self.check(arm["false_accepts"] == expected["false_accepts"] and
                       arm["false_rejects"] == expected["false_rejects"] and
                       arm["exact_correct"] == expected["verdict_correct"] and
                       arm["admission_correct"] == expected["admission_correct"] and
                       dict(arm["verdict_counts"]) == expected["verdict_counts"],
                       label + ":summary_arm:" + name)
        result = {"answers": len(records), "question_clusters": len({r["qid"] for r in records}),
                  "requirements": requirements, "raw_projections_verified": raw_count,
                  "unique_provider_request_ids": len(set(requests)), "arms": arms,
                  "requirement_status_correct": sum(confusion[s + "->" + s]
                                                    for s in ("fulfilled", "missing", "unknown")),
                  "missing_kind_correct": missing_correct, "unknown_sources": dict(unknowns)}
        return result

    def verify_provenance(self, evaluated_source):
        prefix = "provenance/fresh/"
        self.freeze = load(self.file("provenance/freeze.json"))
        self.protocol = load(self.file(prefix + "live.protocol.json"))
        author = load(self.file(prefix + "author-manifest.json"))
        data = load(self.file(prefix + "datafreeze.json"))
        blind = load(self.file(prefix + "blind-manifest.json"))
        comparison = load(self.file(prefix + "review-comparison.json"))
        gold_sha = sha(self.file("evidence/fresh/gold-final.jsonl"))
        self.check(gold_sha == sha(self.file("evidence/fresh/gold-original.jsonl")) ==
                   author["sha256"] == self.protocol["gold_sha256"] ==
                   blind["gold_sha256"] == comparison["gold_sha256"], "gold_hash_chain")
        for name, digest in data["files"].items():
            local = "evidence/fresh/" + name if name.startswith("gold-") else prefix + name
            self.check(self.original_sha(local) == digest, "datafreeze_original_hash:" + name)
        self.check(author["human_verified"] is False and data["human_verified"] is False and
                   data["data_repairs"] == data["exclusions"] == 0, "gold_scope_and_freeze")
        self.check(stamp(self.freeze["frozen_at_utc"]) < stamp(author["authoring_started_at_utc"])
                   < stamp(author["authoring_completed_at_utc"]) < stamp(blind["created_at_utc"])
                   < stamp(comparison["compared_at_utc"]) < stamp(data["frozen_at_utc"])
                   < stamp(self.protocol["created_at_utc"]), "freeze_author_review_api_chronology")
        self.check(self.protocol["retries"] == self.protocol["exclusions"] == 0 and
                   self.protocol["workers"] == 4 and self.protocol["semantic_deadline_seconds"] == 25
                   and self.protocol["per_requirement_timeout_seconds"] == 10, "live_fixed_conditions")
        for name, digest in self.protocol["source_hashes"].items():
            self.check(self.freeze["runtime_files"].get(name) == digest,
                       "protocol_frozen_source_reference:" + name)
        gold = self.gold("evidence/fresh/gold-final.jsonl")
        mapping = load(self.file(prefix + "blind-map.json"))
        reviewed, slots = set(), 0
        for number in (1, 2):
            batch_name, review_name = f"blind-{number}.jsonl", f"review-{number}.jsonl"
            batch = {r["blind_id"]: r for r in rows(self.file(prefix + batch_name))}
            reviews = {r["blind_id"]: r for r in rows(self.file(prefix + review_name))}
            meta = load(self.file(prefix + f"review-{number}-manifest.json"))
            self.check(sha(self.file(prefix + batch_name)) == meta["input_sha256"] ==
                       blind["input_hashes"][batch_name] and
                       sha(self.file(prefix + review_name)) == meta["output_sha256"],
                       "blind_review_hashes:" + str(number))
            self.check(meta.get("input_file", meta.get("input")) == batch_name and
                       meta.get("output_file", meta.get("output")) == review_name,
                       "sanitized_relative_manifest_paths:" + str(number))
            self.check(stamp(blind["created_at_utc"]) < stamp(meta["completed_at_utc"])
                       < stamp(comparison["compared_at_utc"]), "blind_review_chronology:" + str(number))
            self.check(set(batch) == set(reviews) and len(batch) == 40, "blind_review_ids:" + str(number))
            for key, item in batch.items():
                original, review = gold[mapping[key]], reviews[key]
                self.check(set(item) == {"blind_id", "question", "requirements", "answer"},
                           "blind_labels_hidden:" + key)
                self.check(all(item[k] == original[k] for k in
                               ("question", "requirements", "answer")) and
                           review["labels"] == original["labels"] and
                           review["missing_kinds"] == original["missing_kinds"], "blind_agreement:" + key)
                reviewed.add(mapping[key])
                slots += len(review["labels"])
        self.check(len(reviewed) == 80 and slots == 280 and len(mapping) == 80,
                   "blind_review_totals")
        if evaluated_source:
            source = evaluated_source.resolve()
            for name, digest in self.freeze["runtime_files"].items():
                path = source / name
                self.check(path.is_file() and sha(path) == digest, "evaluated_source_hash:" + name)
        return {"frozen_source_files": len(self.freeze["runtime_files"]),
                "protocol_source_references": len(self.protocol["source_hashes"]),
                "source_checkout_checked": bool(evaluated_source), "blind_review_answers": len(reviewed),
                "blind_review_requirement_slots": slots, "human_validation": False}

    def regression(self, current_source=None):
        results, names = {}, {}
        for label, filename, expected in (("baseline886", "baseline886.xml", 886),
                                          ("evaluated965", "evaluated965.xml", 965)):
            xml = ET.parse(self.file("regression/" + filename)).getroot()
            suites = [xml] if xml.tag == "testsuite" else list(xml.findall("testsuite"))
            totals = {key: sum(int(s.get(key, "0")) for s in suites)
                      for key in ("tests", "failures", "errors", "skipped")}
            self.check(totals == {"tests": expected, "failures": 0, "errors": 0, "skipped": 0},
                       "regression_totals:" + label)
            names[label] = {case.get("classname", "") + "::" + case.get("name", "")
                            for suite in suites for case in suite.iter("testcase")}
            results[label] = totals
        added = names["evaluated965"] - names["baseline886"]
        removed = names["baseline886"] - names["evaluated965"]
        self.check(len(added) == 92 and len(removed) == 13, "regression_nodeid_delta")
        results["nodeid_delta"] = {"added": len(added), "removed_or_replaced": len(removed),
                                   "net_added": len(added) - len(removed)}
        post = self.manifest.get("post_evaluation_regression")
        if post:
            xml = ET.parse(self.file(post["junit_file"])).getroot()
            suites = [xml] if xml.tag == "testsuite" else list(xml.findall("testsuite"))
            totals = {key: sum(int(s.get(key, "0")) for s in suites)
                      for key in ("tests", "failures", "errors", "skipped")}
            self.check(totals == post["totals"], "post_evaluation_regression_totals")
            results["post_evaluation"] = totals
            if current_source:
                record = json.loads(self.file(post["source_record"]).read_text(encoding="utf-8"))
                for name, expected in record["tested_files"].items():
                    target = (current_source.resolve() / name).resolve()
                    self.check(target.is_relative_to(current_source.resolve()), "source_path:" + name)
                    self.check(hashlib.sha256(target.read_bytes()).hexdigest() == expected,
                               "post_evaluation_source:" + name)
                results["current_source_files_checked"] = len(record["tested_files"])
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--evaluated-source", type=Path,
                        help="Optional checkout of evaluated commit 3e0a289; current HEAD can differ")
    parser.add_argument("--current-source", type=Path,
                        help="Optional current source to match the later regression's file hashes")
    parser.add_argument("--output", type=Path, help="Optional new report; otherwise stdout only")
    args = parser.parse_args()
    audit = Audit(args.artifact_root)
    try:
        audit.verify_manifest()
        provenance = audit.verify_provenance(args.evaluated_source)
        known, fresh = audit.verify_dataset(False), audit.verify_dataset(True)
        regression = audit.regression(args.current_source)
        result = {"passed": not audit.errors, "checks": audit.checks, "errors": audit.errors,
                  "evaluated_source_commit": audit.manifest["evaluated_source_commit"],
                  "provenance": provenance, "known256": known, "fresh80": fresh,
                  "regression": regression,
                  "scope": "Offline artifact recount and recorded raw-proof checks; no API calls, "
                           "environment/key access, product/model imports, human validation, or "
                           "new independent model accuracy experiment. Fresh B shares the same rows. "
                           "Evaluation predates later repairs; current HEAD is not assumed evaluated."}
    except (OSError, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError) as exc:
        result = {"passed": False, "checks": audit.checks, "errors": audit.errors +
                  ["artifact_verification_exception:" + type(exc).__name__]}
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(rendered)
    print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
