"""
tests/test_mode_affordance_schema.py
Schema validation and fixture tests for mode_affordance labels.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

JSONL_PATH = Path("data/question_sets/q_metadata_structural_reviewed_102q.jsonl")
VALID_MODES = {"definitional", "analytical", "evaluative", "comparative", "critical", "exploratory"}
VALID_CLOSURE = {"closed", "qualified", "open"}


def _load_records() -> list[dict]:
    """Load all records from the reviewed JSONL."""
    records = []
    for line in JSONL_PATH.read_text(encoding="utf-8").strip().split("\n"):
        records.append(json.loads(line))
    return records


# ---------------------------------------------------------------------------
# Schema validator tests
# ---------------------------------------------------------------------------


class TestSchemaValidator:
    """Validate mode_affordance schema constraints on all 102 records."""

    @pytest.fixture(scope="class")
    def records(self):
        return _load_records()

    def test_102_records_present(self, records):
        assert len(records) == 102

    def test_all_records_have_mode_affordance(self, records):
        for rec in records:
            assert "mode_affordance" in rec, f"{rec['id']} missing mode_affordance"

    def test_primary_is_valid_mode(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert ma["primary"] in VALID_MODES, (
                f"{rec['id']}: invalid primary '{ma['primary']}'"
            )

    def test_secondary_is_list(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert isinstance(ma["secondary"], list), (
                f"{rec['id']}: secondary must be list"
            )

    def test_secondary_max_2(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert len(ma["secondary"]) <= 2, (
                f"{rec['id']}: secondary has {len(ma['secondary'])} items"
            )

    def test_secondary_no_duplicates(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert len(ma["secondary"]) == len(set(ma["secondary"])), (
                f"{rec['id']}: secondary has duplicates"
            )

    def test_secondary_does_not_contain_primary(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert ma["primary"] not in ma["secondary"], (
                f"{rec['id']}: secondary contains primary '{ma['primary']}'"
            )

    def test_secondary_values_are_valid_modes(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            for s in ma["secondary"]:
                assert s in VALID_MODES, (
                    f"{rec['id']}: invalid secondary '{s}'"
                )

    def test_closure_is_valid(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert ma["closure"] in VALID_CLOSURE, (
                f"{rec['id']}: invalid closure '{ma['closure']}'"
            )

    def test_action_required_is_boolean(self, records):
        for rec in records:
            ma = rec["mode_affordance"]
            assert isinstance(ma["action_required"], bool), (
                f"{rec['id']}: action_required must be bool"
            )


# ---------------------------------------------------------------------------
# Fixture correctness tests
# ---------------------------------------------------------------------------


class TestFixtureCorrectness:
    """Verify the 5 acceptance fixtures match exactly."""

    @pytest.fixture(scope="class")
    def records_by_id(self):
        return {r["id"]: r for r in _load_records()}

    def test_q031_fixture(self, records_by_id):
        ma = records_by_id["q031"]["mode_affordance"]
        assert ma["primary"] == "definitional"
        assert ma["secondary"] == []
        assert ma["closure"] == "closed"
        assert ma["action_required"] is False

    def test_q033_fixture(self, records_by_id):
        ma = records_by_id["q033"]["mode_affordance"]
        assert ma["primary"] == "comparative"
        assert "critical" in ma["secondary"]
        assert ma["closure"] == "qualified"
        assert ma["action_required"] is False

    def test_q004_fixture(self, records_by_id):
        ma = records_by_id["q004"]["mode_affordance"]
        assert ma["primary"] == "critical"
        assert "analytical" in ma["secondary"]
        assert ma["closure"] == "qualified"
        assert ma["action_required"] is False

    def test_q028_fixture(self, records_by_id):
        ma = records_by_id["q028"]["mode_affordance"]
        assert ma["primary"] == "exploratory"
        assert "analytical" in ma["secondary"]
        assert ma["action_required"] is False

    def test_q065_fixture(self, records_by_id):
        ma = records_by_id["q065"]["mode_affordance"]
        assert ma["action_required"] is True


# ---------------------------------------------------------------------------
# Inline schema validation tests (edge cases)
# ---------------------------------------------------------------------------


class TestSchemaEdgeCases:
    """Test schema validation edge cases using detector logic."""

    def test_invalid_primary_rejected(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "bogus", "secondary": [], "closure": "open",
                                "action_required": False},
        }
        ev = detect("t", "test", meta)
        assert ev.mode_affordance_primary == ""

    def test_duplicated_secondary_rejected(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "critical",
                                "secondary": ["analytical", "analytical"],
                                "closure": "qualified", "action_required": False},
        }
        ev = detect("t", "test", meta)
        assert ev.mode_affordance_secondary == ["analytical"]

    def test_secondary_contains_primary_rejected(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "critical",
                                "secondary": ["critical", "analytical"],
                                "closure": "qualified", "action_required": False},
        }
        ev = detect("t", "test", meta)
        assert "critical" not in ev.mode_affordance_secondary
        assert "analytical" in ev.mode_affordance_secondary

    def test_invalid_closure_rejected(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "analytical", "secondary": [],
                                "closure": "bogus", "action_required": False},
        }
        ev = detect("t", "test", meta)
        assert ev.mode_affordance_closure == ""

    def test_missing_action_required(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "analytical", "secondary": [],
                                "closure": "open"},
        }
        ev = detect("t", "test", meta)
        assert ev.mode_affordance_action_required is None

    def test_too_many_secondaries_capped(self):
        from detector import detect
        meta = {
            "question": "test", "core_propositions": ["p"],
            "disqualifying_shortcuts": [], "acceptable_variants": [],
            "trap_type": "",
            "mode_affordance": {"primary": "analytical",
                                "secondary": ["critical", "evaluative", "exploratory"],
                                "closure": "open", "action_required": False},
        }
        ev = detect("t", "test", meta)
        assert len(ev.mode_affordance_secondary) == 2
