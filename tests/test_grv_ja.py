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


def test_grv_stopword_removal():
    """Step 2: ストップワード・機能語がgrv結果に含まれないこと"""
    pytest.importorskip("fugashi", reason="fugashi not installed")
    from ugh_audit.scorer import UGHScorer

    s = UGHScorer()
    # GPT回答に典型的な機能語・定型句を含むテキスト
    text = (
        "AIの安全性についてはがあります。重要な点があります。"
        "これについて説明します。フレームワークを使用することができます。"
        "意識的な判断が必要であり、バイアスの問題があります。"
    )
    result = s.score(question="テスト", response=text, reference="参照")
    grv = result.grv

    bad_tokens = {"があります", "します", "いことは", "として", "ずしも",
                  "フレ", "ムワ", "パタ", "ション"}
    found_bad = [w for w in grv if w in bad_tokens]
    assert found_bad == [], f"不正トークンが検出された: {found_bad}"


def test_grv_katakana_merge():
    """Step 2: カタカナ複合語が正しく結合されること（断片なし・結合語あり）"""
    pytest.importorskip("fugashi", reason="fugashi not installed")
    from ugh_audit.scorer.ugh_scorer import UGHScorer

    s = UGHScorer()
    grv = s._grv_with_fugashi(
        "トランスフォーマーアーキテクチャはフレームワークの中核です。"
        "アテンションメカニズムとスケーリング則が重要です。"
    )
    # 断片（「フレ」「ムワ」「ション」）が出ないこと
    bad_fragments = {"フレ", "ムワ", "ション", "スパ", "チュ"}
    found_bad = [w for w in grv if w in bad_fragments]
    assert found_bad == [], f"カタカナ断片が検出された: {found_bad}"

    # 結合後のカタカナ語が grv に含まれること（正例）
    katakana_words = [w for w in grv if any(
        "\u30A0" <= c <= "\u30FF" for c in w
    )]
    assert len(katakana_words) >= 1, (
        f"結合後のカタカナ語がgrvに含まれていない。grv={grv}"
    )
