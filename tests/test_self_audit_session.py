"""tests/test_self_audit_session.py — self_audit_session.py の proxy metric テスト"""
from __future__ import annotations

import sys
from pathlib import Path

# analysis/ をインポートパスに追加
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "analysis"))

import self_audit_session as sas  # noqa: E402


def test_evaluative_word_detection():
    """評価語を含む文で L_Q_proxy が正に出る"""
    content = "これは素晴らしい指摘です。非常に勉強になりました。"
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.L_Q_proxy > 0
    assert any("素晴らしい" in h for h in m.evaluative_hits)
    assert any("勉強になり" in h for h in m.evaluative_hits)


def test_clean_factual_report_has_zero_L_Q():
    """事実報告のみの文で L_Q_proxy = 0"""
    content = (
        "tmp file 名を pid + random suffix に変更。テスト 3 件追加。"
        "368 passed, ruff clean。commit abc123。"
    )
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.L_Q_proxy == 0.0
    assert m.evaluative_hits == []


def test_banner_header_detection():
    """banner セクション見出しで decoration_ratio が正に出る"""
    content = "## 所感\n\n内容。\n\n## 累計\n\n7 件対応 ✅✅✅✅✅✅✅"
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.decoration_ratio > 0
    assert any("所感" in h for h in m.banner_hits)
    assert any("累計" in h for h in m.banner_hits)


def test_achievement_report_detected():
    """「累計 X 件」パターンが filler として検出される"""
    content = "累計 7 件すべて対応完了しました。"
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.L_F_proxy > 0
    assert any("achievement_report" in h for h in m.filler_hits)


def test_checkmark_streak_detected():
    """連続 ✅ が filler として検出される"""
    content = "対応済み ✅✅✅✅✅"
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.L_F_proxy > 0
    assert any("checkmark_streak" in h for h in m.filler_hits)


def test_automation_restatement_detected():
    """自動挙動の再宣言が filler として検出される"""
    content = "完了しました。引き続き監視します。次の CI 結果を待ちます。"
    m = sas.compute_turn_metrics(1, "assistant", content)
    assert m.L_F_proxy > 0
    filler_str = " ".join(m.filler_hits)
    assert "引き続き監視" in filler_str or "CI 結果" in filler_str


def test_redundancy_bigram_overlap():
    """直前 turn と bigram overlap がある場合 redundancy_proxy が正に出る"""
    prev = "tmp file 名を変更しました。テスト追加。"
    curr = "tmp file 名を pid で変更。テスト追加。"
    m = sas.compute_turn_metrics(2, "assistant", curr, previous_assistant=prev)
    assert m.redundancy_proxy > 0


def test_empty_content_handled():
    """空文字列でクラッシュしない"""
    m = sas.compute_turn_metrics(1, "assistant", "")
    assert m.char_count == 0
    assert m.sentence_count == 0
    assert m.L_Q_proxy == 0.0


def test_user_turns_excluded_from_metrics():
    """user turn は compute_session_metrics の結果に含まれない"""
    transcript = [
        {"turn": 1, "role": "user", "content": "質問"},
        {"turn": 2, "role": "assistant", "content": "回答"},
        {"turn": 3, "role": "user", "content": "追加質問"},
        {"turn": 4, "role": "assistant", "content": "追加回答"},
    ]
    metrics = sas.compute_session_metrics(transcript)
    assert len(metrics) == 2
    assert all(m.role == "assistant" for m in metrics)
    assert [m.turn for m in metrics] == [2, 4]


def test_split_by_principle_turn():
    """principle_turn で before / after が正しく分離される"""
    metrics = [
        sas.TurnMetrics(turn=1, role="assistant", char_count=10, sentence_count=1, L_Q_proxy=0.5),
        sas.TurnMetrics(turn=2, role="assistant", char_count=10, sentence_count=1, L_Q_proxy=0.4),
        sas.TurnMetrics(turn=3, role="assistant", char_count=10, sentence_count=1, L_Q_proxy=0.0),
        sas.TurnMetrics(turn=4, role="assistant", char_count=10, sentence_count=1, L_Q_proxy=0.0),
    ]
    before, after = sas.split_by_principle_turn(metrics, principle_turn=3)
    assert before.n_turns == 2
    assert after.n_turns == 2
    assert before.mean_L_Q_proxy > after.mean_L_Q_proxy


def test_sample_transcript_runs_end_to_end():
    """同梱のサンプル transcript が end-to-end でエラーなく処理できる"""
    sample_path = _REPO_ROOT / "analysis" / "self_audit_sample.json"
    assert sample_path.exists()

    transcript = sas.load_transcript(sample_path)
    metrics = sas.compute_session_metrics(transcript)
    assert len(metrics) > 0

    before, after = sas.split_by_principle_turn(metrics, principle_turn=6)
    # サンプルは意図的に before で drift、after で clean
    assert before.mean_L_Q_proxy > after.mean_L_Q_proxy
    assert before.mean_L_F_proxy > after.mean_L_F_proxy
