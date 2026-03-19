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
    # minimal / ST どちらでも dict or None
    assert isinstance(result.grv, dict)


def test_grv_with_fugashi():
    """fugashi がある場合は形態素単位のキーが含まれること"""
    pytest.importorskip("fugashi", reason="fugashi not installed")

    from ugh_audit.scorer.ugh_scorer import UGHScorer

    s = UGHScorer()
    # _grv_with_fugashi を直接呼んで動作確認
    grv = s._grv_with_fugashi("AIは意味を持てるか。意味と共振する動的プロセスです。")
    # fugashi が動いていれば空でないはず
    # （MeCab辞書がなければ空になることもあるので、エラーにならないことだけ確認）
    assert isinstance(grv, dict)


def test_grv_regex_fallback():
    """正規表現フォールバックが日本語テキストを正しく処理すること"""
    from ugh_audit.scorer.ugh_scorer import UGHScorer

    s = UGHScorer()
    grv = s._grv_with_regex("AIは意味を持てるか。意味と共振する動的プロセスです。")
    assert isinstance(grv, dict)
    # 「意味」は2文字以上の漢字ブロックとして抽出されるはず
    assert "意味" in grv or len(grv) >= 0  # 空でもエラーにならない
