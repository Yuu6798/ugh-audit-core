"""test_operator_frame.py — 演算子フレーム検出テスト

演算子検出レイヤー (detect_operator) と、
check_propositions への演算子フレーム回収統合のテスト。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from detector import (  # noqa: E402
    check_propositions,
    detect_operator,
)


# ============================================================
# AC-5: 各演算子族の検出テスト (4件)
# ============================================================

class TestDetectOperatorFamilies:
    """negation / deontic / skeptical_modality / binary_frame 各族の検出"""

    def test_negation_detected(self):
        """否定演算子: 「ではない」を検出"""
        result = detect_operator("PoRは誠実性の十分条件ではない")
        assert result is not None
        assert result.family == "negation"
        assert "ではない" in result.token

    def test_negation_prefix_mi(self):
        """否定演算子: 「未〜」を検出"""
        result = detect_operator("ICLの完全なメカニズムは未解明")
        assert result is not None
        assert result.family == "negation"
        assert "未" in result.token

    def test_deontic_detected(self):
        """当為演算子: 「べき」を検出"""
        result = detect_operator("多角的に検討すべき")
        assert result is not None
        assert result.family == "deontic"
        assert "べき" in result.token

    def test_skeptical_modality_detected(self):
        """懐疑的モダリティ: 「かもしれない」を検出"""
        result = detect_operator("統計的な偶然かもしれない")
        assert result is not None
        assert result.family == "skeptical_modality"
        assert "かもしれない" in result.token

    def test_skeptical_towa_kagiranai(self):
        """懐疑的モダリティ: 「とは限らない」を検出"""
        result = detect_operator("精度が高いとは限らない")
        assert result is not None
        assert result.family == "skeptical_modality"
        assert "とは限らない" in result.token

    def test_binary_frame_detected(self):
        """二項対立: 「ではなく」を検出"""
        result = detect_operator("感情ではなく論理で判断する")
        assert result is not None
        assert result.family == "binary_frame"
        assert "ではなく" in result.token

    def test_binary_frame_nikou(self):
        """二項対立: 「二項対立」を検出"""
        result = detect_operator("道具と行為者の二項対立自体が要再検討")
        assert result is not None
        assert result.family == "binary_frame"
        assert "二項対立" in result.token

    def test_no_operator(self):
        """演算子なしの命題: None を返す"""
        result = detect_operator("PoRは意味的対応を測る")
        assert result is None


# ============================================================
# AC-5: 共起ルールテスト (2件)
# ============================================================

class TestCooccurrenceRules:
    """演算子共起ルールの解決テスト"""

    def test_deontic_plus_negation(self):
        """deontic + negation → deontic 優先 (「べきではない」)"""
        result = detect_operator("安易に断定すべきではない")
        assert result is not None
        assert result.family == "deontic"
        # 「べきではない」全体が deontic パターンにマッチ
        assert "べきではない" in result.token

    def test_skeptical_plus_binary_frame(self):
        """skeptical_modality + binary_frame → binary_frame 優先"""
        result = detect_operator("AではなくBかもしれない")
        assert result is not None
        assert result.family == "binary_frame"
        assert "ではなく" in result.token


# ============================================================
# AC-5: 演算子フレーム回収テスト (5件: operator分類の命題回収)
# ============================================================

class TestOperatorFrameRecovery:
    """演算子を含む命題が check_propositions で回収されるテスト"""

    def test_negation_recovery_guarantee(self):
        """否定命題「保証しない」が演算子回収で hit になる

        命題: 低ΔEは良い回答を保証しない
        回答: ΔEが低いだけでは回答品質は保証されない
        通常バイグラムでは閾値未達でも、演算子回収で hit する。
        """
        props = ["低ΔEは良い回答を保証しない"]
        # 回答は概念を含むが表現が異なる: 「保証されない」は否定マーカー
        response = "ΔEが低いだけでは回答品質は保証されないため、複合評価が重要である"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids, f"否定命題が回収されるべき: hit_ids={hit_ids}"

    def test_negation_recovery_mi_prefix(self):
        """否定命題「未解明」が演算子回収で hit になる

        命題: ICLの完全なメカニズムは未解明
        回答: ICLのメカニズムについては未だ完全には解明されていない
        """
        props = ["ICLの完全なメカニズムは未解明"]
        response = "ICLのメカニズムについては未だ完全には解明されていない研究課題である"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids, f"未解明命題が回収されるべき: hit_ids={hit_ids}"

    def test_binary_frame_recovery(self):
        """二項対立命題が演算子回収で hit になる

        命題: 完全公開か完全秘匿かの二択ではない
        回答: 完全な公開と秘匿の二項対立ではなく段階的な制御が現実的
        """
        props = ["完全公開か完全秘匿かの二択ではない"]
        response = "完全な公開と秘匿の二項対立ではなく、段階的なアクセス制御が現実的な選択肢となる"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids, f"二項対立命題が回収されるべき: hit_ids={hit_ids}"

    def test_negation_recovery_fusokumin(self):
        """否定命題「不十分」が演算子回収で hit になる

        命題: 一つの論点だけに絞るのは不十分
        回答: 単一の論点に限定することは不十分であり多角的な検討が求められる
        """
        props = ["一つの論点だけに絞るのは不十分"]
        response = "単一の論点に限定することは不十分であり、多角的な検討が求められる"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids, f"不十分命題が回収されるべき: hit_ids={hit_ids}"

    def test_negation_recovery_fukanou(self):
        """否定命題「不可能」が演算子回収で hit になる

        命題: 断定は不可能であり「わからない」が誠実
        回答: 現時点では断定は不可能であり、不明であることを認めるのが誠実な態度
        """
        props = ["断定は不可能であり「わからない」が誠実"]
        response = "現時点では断定は不可能であり、不明であることを認めるのが誠実な態度である"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids, f"不可能命題が回収されるべき: hit_ids={hit_ids}"


# ============================================================
# 回帰テスト: 演算子なし命題が影響を受けない
# ============================================================

class TestNoRegression:
    """演算子レイヤーが既存マッチに影響しないことの確認"""

    def test_positive_prop_still_hits(self):
        """演算子なしの肯定命題は従来通り hit する"""
        props = ["PoRは意味的対応を測る"]
        response = "PoRは質問と回答の意味的対応を測定する指標である"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in hit_ids

    def test_unrelated_prop_still_misses(self):
        """無関係な命題は miss のまま（偽回収しない）"""
        props = ["量子コンピュータの超伝導回路設計"]
        response = "AIの倫理的課題について検討する必要がある"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in miss_ids

    def test_weak_overlap_not_recovered(self):
        """大命題で2語のみ一致 + マーカー存在でも偽回収しない

        レビュー指摘: 9バイグラム中2語一致 (fr=0.22) はfull_recall不足で却下。
        """
        props = ["モデル評価指標の単一最適化に依存すべきではない"]
        response = "モデル評価は必要だ"
        hits, hit_ids, miss_ids = check_propositions(response, props)
        assert 0 in miss_ids, f"弱overlap命題が偽回収された: hit_ids={hit_ids}"
