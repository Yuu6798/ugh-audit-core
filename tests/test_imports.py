"""
tests/test_imports.py
全公開 import パスが通ることを確認
"""


def test_toplevel_imports():
    from ugh_audit import AuditCollector, AuditDB, AuditResult, GoldenEntry, GoldenStore
    from ugh_audit import SessionCollector, UGHScorer, generate_csv, generate_text_report

    assert UGHScorer is not None
    assert AuditResult is not None
    assert AuditDB is not None
    assert AuditCollector is not None
    assert SessionCollector is not None
    assert GoldenStore is not None
    assert GoldenEntry is not None
    assert generate_text_report is not None
    assert generate_csv is not None


def test_subpackage_imports():
    from ugh_audit.collector import AuditCollector, SessionCollector
    from ugh_audit.reference import GoldenEntry, GoldenStore
    from ugh_audit.report import generate_csv, generate_text_report
    from ugh_audit.scorer import AuditResult, UGHScorer
    from ugh_audit.storage import AuditDB

    assert UGHScorer is not None
    assert AuditResult is not None
    assert AuditDB is not None
    assert AuditCollector is not None
    assert SessionCollector is not None
    assert GoldenStore is not None
    assert GoldenEntry is not None
    assert generate_text_report is not None
    assert generate_csv is not None
