from pathlib import Path

from ugh_audit.engine import MetaPatchCompiler


def test_legacy_action_normalization():
    """legacy_action_map が yaml に存在しない場合、入力をそのまま返す。"""
    compiler = MetaPatchCompiler()
    result = compiler.normalize_legacy_actions(["clarify", "broaden", "promote_to_high"])
    # main の operator_catalog.yaml には legacy_action_map がないため passthrough
    assert result == ["clarify", "broaden", "promote_to_high"]


def test_primary_fail_returns_empty_without_map():
    """primary_fail_map が yaml に存在しない場合、空リストを返す。"""
    compiler = MetaPatchCompiler()
    plan = compiler.compile_row({"id": "q024", "primary_fail": "f4", "note": "x"})
    # main の operator_catalog.yaml には primary_fail_map がない
    assert plan.opcodes == []
    assert plan.budget.cost == 0


def test_compile_existing_review_csv():
    compiler = MetaPatchCompiler()
    csv_path = Path("data/eval/structural_fail_4element.csv")
    plans = compiler.compile_csv(csv_path, extra_actions_by_id={"q024": ["clarify"]})
    assert len(plans) >= 20
    q024 = next(p for p in plans if p.id == "q024")
    # legacy_action_map なしのため clarify がそのまま passthrough
    assert "clarify" in q024.opcodes
