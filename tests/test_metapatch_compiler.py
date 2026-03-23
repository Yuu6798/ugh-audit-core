from pathlib import Path

from ugh_audit.engine import MetaPatchCompiler


def test_legacy_action_normalization():
    compiler = MetaPatchCompiler()
    assert compiler.normalize_legacy_actions(["clarify", "broaden", "promote_to_high"]) == [
        "clarify_scope",
        "broaden_context",
        "elevate_salience",
    ]


def test_primary_fail_maps_to_canonical_opcode():
    compiler = MetaPatchCompiler()
    plan = compiler.compile_row({"id": "q024", "primary_fail": "f4", "note": "x"})
    assert "repair_forbidden_reinterpret" in plan.opcodes
    assert plan.budget.cost >= 1


def test_compile_existing_review_csv():
    compiler = MetaPatchCompiler()
    csv_path = Path("data/eval/structural_fail_4element.csv")
    plans = compiler.compile_csv(csv_path, extra_actions_by_id={"q024": ["clarify"]})
    assert len(plans) >= 20
    q024 = next(p for p in plans if p.id == "q024")
    assert "clarify_scope" in q024.opcodes
