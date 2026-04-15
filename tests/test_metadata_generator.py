"""
tests/test_metadata_generator.py
metadata_generator モジュールの単体テスト
"""
from __future__ import annotations

from ugh_audit.metadata_generator import (
    METADATA_GENERATION_SCHEMA_VERSION,
    build_metadata_request,
    default_output_template,
    detect_missing_metadata,
)


class TestDetectMissingMetadata:
    def test_none_meta(self):
        assert detect_missing_metadata(None) == [
            "core_propositions", "trap_type", "mode_affordance",
        ]

    def test_empty_dict(self):
        assert detect_missing_metadata({}) == [
            "core_propositions", "trap_type", "mode_affordance",
        ]

    def test_full_meta(self):
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": "none",
            "mode_affordance": {"primary": "definitional"},
        }
        assert detect_missing_metadata(meta) == []

    def test_missing_trap_type(self):
        meta = {
            "core_propositions": ["命題A"],
            "mode_affordance": {"primary": "analytical"},
        }
        assert detect_missing_metadata(meta) == ["trap_type"]

    def test_missing_core_propositions(self):
        meta = {
            "trap_type": "none",
            "mode_affordance": {"primary": "evaluative"},
        }
        assert detect_missing_metadata(meta) == ["core_propositions"]

    def test_empty_core_propositions(self):
        meta = {
            "core_propositions": [],
            "trap_type": "none",
            "mode_affordance": {"primary": "definitional"},
        }
        assert detect_missing_metadata(meta) == ["core_propositions"]

    def test_empty_trap_type_is_not_missing(self):
        """trap_type="" は「罠なし」の明示指定であり欠損ではない"""
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": "",
            "mode_affordance": {"primary": "definitional"},
        }
        assert detect_missing_metadata(meta) == []

    def test_null_trap_type_is_missing(self):
        """trap_type=None は欠損として扱う"""
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": None,
            "mode_affordance": {"primary": "definitional"},
        }
        assert detect_missing_metadata(meta) == ["trap_type"]

    # --- mode_affordance 固有テスト ---

    def test_missing_mode_affordance(self):
        """mode_affordance キー不在 → 欠損"""
        meta = {"core_propositions": ["命題A"], "trap_type": "none"}
        assert "mode_affordance" in detect_missing_metadata(meta)

    def test_mode_affordance_none_is_missing(self):
        """mode_affordance=None → 欠損"""
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": "none",
            "mode_affordance": None,
        }
        assert "mode_affordance" in detect_missing_metadata(meta)

    def test_mode_affordance_empty_primary_is_missing(self):
        """mode_affordance.primary="" → 欠損"""
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": "none",
            "mode_affordance": {"primary": ""},
        }
        assert "mode_affordance" in detect_missing_metadata(meta)

    def test_mode_affordance_valid(self):
        """mode_affordance が正常値 → 欠損でない"""
        meta = {
            "core_propositions": ["命題A"],
            "trap_type": "none",
            "mode_affordance": {"primary": "comparative", "secondary": None},
        }
        assert "mode_affordance" not in detect_missing_metadata(meta)


class TestBuildMetadataRequest:
    def test_no_missing_returns_none(self):
        assert build_metadata_request("質問", []) is None

    def test_builds_request(self):
        result = build_metadata_request(
            "AIの誠実性とは？",
            ["core_propositions"],
            question_id="q001",
        )
        assert result is not None
        assert result["schema_version"] == METADATA_GENERATION_SCHEMA_VERSION
        assert result["generation_policy"] == "ai_draft"
        assert result["question_id"] == "q001"
        assert "core_propositions" in result["required_fields"]
        assert result["input"]["question"] == "AIの誠実性とは？"
        assert "output_template" in result

    def test_default_metadata_source(self):
        result = build_metadata_request("質問", ["trap_type"])
        assert result["metadata_source"] == "none"

    def test_custom_metadata_source(self):
        result = build_metadata_request(
            "質問", ["trap_type"], metadata_source="inline",
        )
        assert result["metadata_source"] == "inline"


class TestDefaultOutputTemplate:
    def test_has_required_keys(self):
        tpl = default_output_template()
        for key in ("question", "core_propositions", "trap_type",
                     "disqualifying_shortcuts", "acceptable_variants",
                     "mode_affordance", "metadata_confidence", "rationale"):
            assert key in tpl

    def test_mode_affordance_template_structure(self):
        tpl = default_output_template()
        ma = tpl["mode_affordance"]
        assert isinstance(ma, dict)
        assert "primary" in ma
        assert "secondary" in ma
        assert "closure" in ma
        assert "action_required" in ma
