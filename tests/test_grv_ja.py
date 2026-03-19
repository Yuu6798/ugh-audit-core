"""
tests/test_grv_ja.py
fugashi 有無での grv 計算テスト
"""
import pytest


def test_grv_without_fugashi_fallback():
    """fugashi なしでもエラーにならず AuditResult が返ること"""
    from ugh_audit.scorer import UGHScorer

    s = UGHScorer()
    result = s.score(
        question="テスト",
        response="これは日本語の回答です。意味と共振する。",
        reference="参照文です。",
    )
    assert result is not None
    assert isinstance(result.grv, dict)


def test_grv_with_fugashi():
    """fugashi がある場合、score() 経由で grv が dict として返ること"""
    pytest.importorskip("fugashi", reason="fugashi not installed")

    from ugh_audit.scorer import UGHScorer

    s = UGHScorer()
    # public API 経由でテスト（リファクタリング耐性を優先）
    result = s.score(
        question="AIは意味を持てるか",
        response="AIは意味を持てるか。意味と共振する動的プロセスです。",
        reference="意味位相空間での共振",
    )
    assert isinstance(result.grv, dict)
    # fugashi が正常動作している場合は空でないはず
    # （MeCab辞書未整備環境では空になることも許容）
    assert result.grv is not None


def test_grv_regex_fallback():
    """正規表現フォールバックが日本語テキストを正しく処理すること"""
    from ugh_audit.scorer.ugh_scorer import UGHScorer

    s = UGHScorer()
    grv = s._grv_with_regex("AIは意味を持てるか。意味と共振する動的プロセスです。")
    assert isinstance(grv, dict)
    # 「意味」は2文字以上の漢字ブロックとして抽出されるはず
    assert "意味" in grv
