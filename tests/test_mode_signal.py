"""
tests/test_mode_signal.py
Unit tests for mode_signal.py — response_mode_signal scorer.
"""
from __future__ import annotations


from mode_signal import (
    compute_mode_signal,
)


# ---------------------------------------------------------------------------
# Not-available tests
# ---------------------------------------------------------------------------


class TestModeSignalNotAvailable:
    def test_empty_primary_returns_not_available(self):
        result = compute_mode_signal(
            response_text="some response",
            mode_affordance_primary="",
        )
        assert result.status == "not_available"
        assert result.primary_score is None
        assert result.overall_score is None

    def test_invalid_primary_returns_not_available(self):
        result = compute_mode_signal(
            response_text="some response",
            mode_affordance_primary="procedural",
        )
        assert result.status == "not_available"

    def test_not_available_has_empty_collections(self):
        result = compute_mode_signal(
            response_text="some response",
            mode_affordance_primary="",
        )
        assert result.matched_moves == []
        assert result.missing_moves == []
        assert result.evidence == []
        assert result.secondary_scores == {}


# ---------------------------------------------------------------------------
# Mode scoring tests — good/bad for each mode
# ---------------------------------------------------------------------------


class TestDefinitionalScoring:
    def test_good_response(self):
        response = "PoRとは、意味的共振を測る指標である。PoRの範囲は回答品質の特定側面に限定される。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="definitional",
        )
        assert result.status == "available"
        assert result.primary_score == 1.0
        assert "define_target" in result.matched_moves
        assert "set_boundary" in result.matched_moves

    def test_bad_response(self):
        response = "AIは素晴らしい技術です。今後の発展が期待されます。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="definitional",
        )
        assert result.primary_score == 0.0
        assert len(result.missing_moves) == 2


class TestAnalyticalScoring:
    def test_good_response(self):
        response = "この問題の原因は学習データの偏りにある。そのメカニズムとして、勾配消失が作用している。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="analytical",
        )
        assert result.primary_score == 1.0

    def test_bad_response(self):
        response = "はい、そうだと思います。間違いないでしょう。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="analytical",
        )
        assert result.primary_score == 0.0


class TestEvaluativeScoring:
    def test_good_response(self):
        response = "安全性の基準に照らして判断すると、このアプローチは有効であるが限界がある。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
        )
        assert result.primary_score == 1.0

    def test_bad_response(self):
        response = "面白い問題ですね。色々な角度から考えられます。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
        )
        assert result.primary_score == 0.0


class TestComparativeScoring:
    def test_good_response(self):
        response = "一方でPoRは意味的共振を測り、他方でBERTScoreは統計的類似度を測る。両者の共通点は埋め込みベースである点だ。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="comparative",
        )
        assert result.primary_score == 1.0

    def test_bad_response(self):
        response = "どちらも良いツールです。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="comparative",
        )
        assert result.primary_score <= 0.5


class TestCriticalScoring:
    def test_good_response(self):
        response = "この問いの前提として「自己適用可能」と仮定されているが、本当にそう言えるかは再検討が必要である。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="critical",
        )
        assert result.primary_score == 1.0
        assert "inspect_premise" in result.matched_moves
        assert "reframe_if_needed" in result.matched_moves

    def test_bad_response(self):
        response = "はい、その通りです。正しい主張だと思います。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="critical",
        )
        assert result.primary_score == 0.0


class TestExploratoryScoring:
    def test_good_response(self):
        response = "考えられるシナリオは複数ある。仮にニュース要約に適用した場合、今後のさらなる検討が必要である。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="exploratory",
        )
        assert result.primary_score == 1.0

    def test_bad_response(self):
        response = "結論として、これは不可能です。以上です。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="exploratory",
        )
        assert result.primary_score == 0.0


# ---------------------------------------------------------------------------
# Closure scoring tests
# ---------------------------------------------------------------------------


class TestClosureScoring:
    def test_closed_good(self):
        response = "したがって、PoRは十分条件ではないと言える。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_closure="closed",
        )
        assert result.closure_score == 1.0

    def test_closed_bad(self):
        response = "色々な側面がありますね。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_closure="closed",
        )
        assert result.closure_score == 0.0

    def test_qualified_good(self):
        response = "したがって有効であると結論できる。ただし、データセットによっては限界がある。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_closure="qualified",
        )
        assert result.closure_score == 1.0

    def test_qualified_partial(self):
        response = "したがって有効であると結論できる。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_closure="qualified",
        )
        assert result.closure_score == 0.5

    def test_qualified_bad(self):
        response = "面白い問題です。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_closure="qualified",
        )
        assert result.closure_score == 0.0

    def test_open_good(self):
        response = "今後のさらなる研究が必要である。未解決の論点が残る。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="exploratory",
            mode_affordance_closure="open",
        )
        assert result.closure_score == 1.0

    def test_open_bad(self):
        response = "これは確定的な事実です。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="exploratory",
            mode_affordance_closure="open",
        )
        assert result.closure_score == 0.0

    def test_empty_closure_gives_none(self):
        result = compute_mode_signal(
            response_text="テスト",
            mode_affordance_primary="analytical",
            mode_affordance_closure="",
        )
        assert result.closure_score is None


# ---------------------------------------------------------------------------
# Action scoring tests
# ---------------------------------------------------------------------------


class TestActionScoring:
    def test_action_required_true_strong(self):
        response = "段階的アクセス制御を導入すべきである。具体的なステップとして、まず監査体制を整える必要がある。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_action_required=True,
        )
        assert result.action_score == 1.0

    def test_action_required_true_weak(self):
        response = "可能であれば検討してください。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_action_required=True,
        )
        assert result.action_score == 0.5

    def test_action_required_true_none(self):
        response = "理論的な問題です。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="evaluative",
            mode_affordance_action_required=True,
        )
        assert result.action_score == 0.0

    def test_action_required_false_gives_null(self):
        result = compute_mode_signal(
            response_text="何かすべきである。",
            mode_affordance_primary="evaluative",
            mode_affordance_action_required=False,
        )
        assert result.action_score is None

    def test_action_required_none_gives_null(self):
        result = compute_mode_signal(
            response_text="何かすべきである。",
            mode_affordance_primary="evaluative",
            mode_affordance_action_required=None,
        )
        assert result.action_score is None


# ---------------------------------------------------------------------------
# Overall score tests
# ---------------------------------------------------------------------------


class TestOverallScore:
    def test_overall_in_range(self):
        response = "PoRとは共振指標である。範囲は限定される。したがって有効である。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="definitional",
            mode_affordance_secondary=["evaluative"],
            mode_affordance_closure="closed",
            mode_affordance_action_required=False,
        )
        assert result.overall_score is not None
        assert 0.0 <= result.overall_score <= 1.0

    def test_normalization_when_secondary_absent(self):
        result = compute_mode_signal(
            response_text="PoRとは共振指標である。範囲は限定される。",
            mode_affordance_primary="definitional",
            mode_affordance_secondary=[],
            mode_affordance_closure="",
            mode_affordance_action_required=False,
        )
        # Only primary contributes → overall = primary_score
        assert result.overall_score == result.primary_score

    def test_overall_none_when_not_available(self):
        result = compute_mode_signal(
            response_text="test",
            mode_affordance_primary="",
        )
        assert result.overall_score is None


# ---------------------------------------------------------------------------
# Determinism test
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        kwargs = dict(
            response_text="前提として暗黙の仮定がある。本当にそう言えるか再検討が必要だ。したがって留保が必要である。",
            mode_affordance_primary="critical",
            mode_affordance_secondary=["analytical"],
            mode_affordance_closure="qualified",
            mode_affordance_action_required=False,
        )
        r1 = compute_mode_signal(**kwargs)
        r2 = compute_mode_signal(**kwargs)
        assert r1.primary_score == r2.primary_score
        assert r1.secondary_scores == r2.secondary_scores
        assert r1.closure_score == r2.closure_score
        assert r1.overall_score == r2.overall_score
        assert r1.matched_moves == r2.matched_moves
        assert r1.missing_moves == r2.missing_moves


# ---------------------------------------------------------------------------
# Secondary scoring tests
# ---------------------------------------------------------------------------


class TestSecondaryScoring:
    def test_secondary_scores_returned(self):
        response = "前提として暗黙の仮定がある。再検討すると、原因はデータの偏りにある。メカニズムとして勾配消失が作用する。"
        result = compute_mode_signal(
            response_text=response,
            mode_affordance_primary="critical",
            mode_affordance_secondary=["analytical"],
        )
        assert "analytical" in result.secondary_scores
        assert result.secondary_scores["analytical"] == 1.0

    def test_secondary_excludes_primary_duplicate(self):
        result = compute_mode_signal(
            response_text="テスト",
            mode_affordance_primary="critical",
            mode_affordance_secondary=["critical"],
        )
        # "critical" is primary, so it should not appear in secondary_scores
        assert "critical" not in result.secondary_scores
