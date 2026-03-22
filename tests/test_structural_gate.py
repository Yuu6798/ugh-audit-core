"""構造ゲート判定スクリプトのテスト。

番兵問7件の判定と、20件アノテーションとの整合性を検証する。
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.structural_gate import (
    check_f1_anchor,
    check_f2_unknown,
    check_f3_operator,
    check_f4_premise,
    compute_verdict,
    load_q_metadata,
    run_gate,
)

# ---------------------------------------------------------------------------
# パス定数
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
Q_META_PATH = DATA_DIR / "question_sets" / "q_metadata_structural_reviewed_102q.jsonl"
RAW_RESP_PATH = DATA_DIR / "phase_c_v0" / "phase_c_raw.jsonl"
ANNO_PATH = DATA_DIR / "eval" / "structural_fail_4element.csv"

# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def q_metadata() -> Dict[str, Dict[str, Any]]:
    return load_q_metadata(Q_META_PATH)


@pytest.fixture(scope="module")
def temp0_responses() -> List[Dict[str, str]]:
    """temperature=0.0 の回答のみ返す。"""
    responses: List[Dict[str, str]] = []
    with open(RAW_RESP_PATH, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line.strip())
            if d["temperature"] == 0.0:
                responses.append({"id": d["id"], "response": d["response"]})
    return responses


@pytest.fixture(scope="module")
def gate_results(
    q_metadata: Dict[str, Dict[str, Any]],
    temp0_responses: List[Dict[str, str]],
) -> Dict[str, Dict[str, Any]]:
    """全問の構造ゲート結果を id → result 辞書で返す。"""
    results = run_gate(q_metadata, temp0_responses)
    return {r["id"]: r for r in results}


@pytest.fixture(scope="module")
def annotations() -> List[Dict[str, str]]:
    with open(ANNO_PATH, encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("id")]


# ---------------------------------------------------------------------------
# 番兵問テスト
# ---------------------------------------------------------------------------


class TestSentinelVerdicts:
    """番兵問7件の verdict が期待値と一致すること。"""

    @pytest.mark.parametrize(
        "qid, expected_verdict",
        [
            ("q032", "fail"),
            ("q024", "fail"),
            ("q095", "fail"),
            ("q015", "pass"),
            ("q025", "warn"),
            ("q033", "warn"),
            ("q100", "warn"),
        ],
    )
    def test_sentinel_verdict(
        self,
        gate_results: Dict[str, Dict[str, Any]],
        qid: str,
        expected_verdict: str,
    ) -> None:
        result = gate_results[qid]
        assert result["verdict"] == expected_verdict, (
            f"{qid}: expected {expected_verdict}, got {result['verdict']} "
            f"(scores: {result['element_scores']})"
        )


class TestSentinelDetails:
    """番兵問の主要フラグが正しく検出されていること。"""

    def test_q032_f2_expansion(self, gate_results: Dict[str, Dict[str, Any]]) -> None:
        r = gate_results["q032"]
        assert r["element_scores"]["f2_unknown"] >= 1.0

    def test_q024_fail(self, gate_results: Dict[str, Dict[str, Any]]) -> None:
        r = gate_results["q024"]
        assert r["fail_max"] >= 1.0

    def test_q095_f3_or_f4(self, gate_results: Dict[str, Dict[str, Any]]) -> None:
        r = gate_results["q095"]
        scores = r["element_scores"]
        assert scores["f3_operator"] >= 0.5 or scores["f4_premise"] >= 1.0

    def test_q015_all_zero(self, gate_results: Dict[str, Dict[str, Any]]) -> None:
        r = gate_results["q015"]
        for v in r["element_scores"].values():
            assert v == 0.0


# ---------------------------------------------------------------------------
# 20件アノテーション整合性
# ---------------------------------------------------------------------------


class TestAnnotationConsistency:
    """20件のアノテーション結果との整合性チェック。"""

    def test_fail_condition(
        self,
        gate_results: Dict[str, Dict[str, Any]],
        annotations: List[Dict[str, str]],
    ) -> None:
        """human_score <= 2 かつ has_any_fail=1 → verdict=fail。"""
        for row in annotations:
            hs = float(row["human_score"])
            haf = int(row["has_any_fail"])
            if hs <= 2 and haf == 1:
                r = gate_results.get(row["id"])
                assert r is not None, f"{row['id']}: not found in results"
                assert r["verdict"] == "fail", (
                    f"{row['id']}: expected fail (human_score={hs}, has_any_fail={haf}), "
                    f"got {r['verdict']}"
                )

    def test_pass_condition(
        self,
        gate_results: Dict[str, Dict[str, Any]],
        annotations: List[Dict[str, str]],
    ) -> None:
        """human_score >= 4 かつ has_any_flag=0 → verdict=pass。"""
        for row in annotations:
            hs = float(row["human_score"])
            hafl = int(row["has_any_flag"])
            if hs >= 4 and hafl == 0:
                r = gate_results.get(row["id"])
                assert r is not None, f"{row['id']}: not found in results"
                assert r["verdict"] == "pass", (
                    f"{row['id']}: expected pass (human_score={hs}, has_any_flag={hafl}), "
                    f"got {r['verdict']}"
                )


# ---------------------------------------------------------------------------
# 個別チェック関数ユニットテスト
# ---------------------------------------------------------------------------


class TestF1Anchor:
    def test_all_present(self) -> None:
        meta = {"anchor_terms": ["AI", "回答"], "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": []}
        flag, _, _ = check_f1_anchor("AIの回答は誠実です", meta)
        assert flag == 0.0

    def test_low_hit_rate(self) -> None:
        meta = {"anchor_terms": ["AI", "回答", "誠実", "PoR", "ΔE"],
                "anchor_allowed_rephrase": [], "anchor_forbidden_reinterpret": []}
        flag, _, _ = check_f1_anchor("AIについて述べます", meta)
        assert flag == 0.5

    def test_forbidden(self) -> None:
        meta = {"anchor_terms": ["AI"], "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": ["人工無能"]}
        flag, _, _ = check_f1_anchor("人工無能は役に立つ", meta)
        assert flag == 1.0

    def test_forbidden_bracket_pattern(self) -> None:
        """『...』形式のレビューアノートからパターンを抽出してマッチする。"""
        meta = {"anchor_terms": ["共振"], "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": [
                    "『共振=高い相関』『共振=神秘的同調』を禁止再解釈に入れるべき。"
                ]}
        flag, _, _ = check_f1_anchor("共振=高い相関として理解できます", meta)
        assert flag == 1.0

    def test_forbidden_bracket_no_match(self) -> None:
        """『...』パターンにマッチしなければ flag=0。"""
        meta = {"anchor_terms": ["共振"], "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": [
                    "『共振=高い相関』を禁止再解釈に入れるべき。"
                ]}
        flag, _, _ = check_f1_anchor("共振は意味的な共鳴を測定します", meta)
        assert flag == 0.0

    def test_empty_terms(self) -> None:
        meta = {"anchor_terms": [], "anchor_allowed_rephrase": [],
                "anchor_forbidden_reinterpret": []}
        flag, _, _ = check_f1_anchor("何でもOK", meta)
        assert flag == 0.0


class TestF2Unknown:
    def test_no_terms(self) -> None:
        meta = {"unknown_terms": [], "severity_hint": "low"}
        flag, _, _ = check_f2_unknown("何でも", meta)
        assert flag == 0.0

    def test_fabricated_expansion(self) -> None:
        meta = {"unknown_terms": ["UGHer"], "unknown_default_action": "保持",
                "severity_hint": "high"}
        resp = "UGHer（User-Generated Hypertextual Environment Rating）は..."
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 1.0

    def test_known_variant_expansion(self) -> None:
        meta = {"unknown_terms": ["SVP", "Semantic Vector Prompt"],
                "unknown_default_action": "保持", "severity_hint": "high"}
        resp = "SVP（Semantic Vector Prompt）は..."
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.0

    def test_expansion_with_related_term(self) -> None:
        meta = {"unknown_terms": ["PoR", "共振"],
                "unknown_default_action": "保持", "severity_hint": "high"}
        resp = "PoR（Pattern of Resonance）が測る「共振」と..."
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.5

    def test_uncertainty_not_expressed(self) -> None:
        meta = {"unknown_terms": ["PoR"], "unknown_default_action": "不確実性明示",
                "severity_hint": "high"}
        resp = "PoRはスコアです。"
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.5

    def test_uncertainty_expressed(self) -> None:
        meta = {"unknown_terms": ["PoR"], "unknown_default_action": "不確実性明示",
                "severity_hint": "high"}
        resp = "PoRの詳細は不明ですが、スコアの一種と思われる。"
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.0

    def test_expansion_allowed_skips_check(self) -> None:
        """unknown_default_action=展開可 なら勝手展開チェックをスキップする。"""
        meta = {"unknown_terms": ["Mesh"], "unknown_default_action": "展開可",
                "severity_hint": "high"}
        resp = "Mesh（Multi-layered Evaluation of Semantic Harmony）は..."
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.0

    def test_medium_severity_skips_expansion(self) -> None:
        meta = {"unknown_terms": ["Attention Weights"],
                "unknown_default_action": "保持", "severity_hint": "medium"}
        resp = "Attention Weights（注意の重み）はTransformerの..."
        flag, _, _ = check_f2_unknown(resp, meta)
        assert flag == 0.0


class TestF3Operator:
    def test_no_operators(self) -> None:
        meta = {"operators": []}
        flag, _, _ = check_f3_operator("何でも", meta)
        assert flag == 0.0

    def test_universal_unhandled(self) -> None:
        meta = {"operators": [{"term": "常に", "type": "universal"}]}
        resp = "AIは常にバイアスを含みます。その理由は以下の通りです。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 1.0

    def test_universal_handled_without_repeat(self) -> None:
        """演算子を繰り返さず限定表現のみ → 0.0。"""
        meta = {"operators": [{"term": "常に", "type": "universal"}]}
        resp = "バイアスを含むとは限らない。場合による。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.0

    def test_universal_refuted_with_repeat(self) -> None:
        """演算子が含まれつつ限定表現あり → 0.5（部分的処理）。"""
        meta = {"operators": [{"term": "常に", "type": "universal"}]}
        resp = "AIは常にバイアスを含むとは限らない。場合による。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.5

    def test_universal_partial(self) -> None:
        meta = {"operators": [{"term": "常に", "type": "universal"}]}
        resp = "AIは常にバイアスを含みます。しかし、対策は可能です。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.5

    def test_reason_request_no_premise_challenge(self) -> None:
        """reason_request_with_premise で前提を検討せず理由列挙 → 1.0。"""
        meta = {"operators": [{"term": "なぜ", "type": "reason_request_with_premise"}]}
        resp = "なぜ優れているかは以下の理由からです。しかし、注意点もあります。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 1.0

    def test_reason_request_with_premise_challenge(self) -> None:
        """reason_request_with_premise で前提を検討してから回答 → 0.0。"""
        meta = {"operators": [{"term": "なぜ", "type": "reason_request_with_premise"}]}
        resp = "そもそも前提が正しいかを検討する必要がある。なぜなら..."
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.0

    def test_limiter_suffix(self) -> None:
        meta = {"operators": [{"term": "にすぎない", "type": "limiter_suffix"}]}
        resp = "AIは道具にすぎないと言われます。道具としての価値は高いです。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.5

    def test_limiter_type(self) -> None:
        """type=limiter は limiter_suffix/prefix と同等に扱う。"""
        meta = {"operators": [{"term": "本質的に", "type": "limiter"}]}
        resp = "本質的に拡張するかは議論がある。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.5

    def test_limiter_type_handled(self) -> None:
        meta = {"operators": [{"term": "本質的に", "type": "limiter"}]}
        resp = "そもそも本質的にとは何を意味するのか。しかし限界もある。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.0

    def test_skeptical_modality_unhandled(self) -> None:
        """type=skeptical_modality で疑いを認識しない応答 → 0.5。"""
        meta = {"operators": [{"term": "果たして", "type": "skeptical_modality"}]}
        resp = "果たして可能かは分かりませんが、技術は進歩しています。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.5

    def test_skeptical_modality_handled(self) -> None:
        meta = {"operators": [{"term": "本当に", "type": "skeptical_modality"}]}
        resp = "本当にバグかはそもそも定義に依存する。しかし実用上は問題がある。"
        flag, _, _ = check_f3_operator(resp, meta)
        assert flag == 0.0


class TestF4Premise:
    def test_no_premise(self) -> None:
        meta = {"premise_present": False}
        flag, _, _ = check_f4_premise("何でも", meta)
        assert flag == 0.0

    def test_premise_addressed(self) -> None:
        meta = {"premise_present": True, "premise_content": "AIは危険",
                "premise_acceptable_stances": []}
        resp = "前提として、AIの危険性は文脈に依存します。"
        flag, _, _ = check_f4_premise(resp, meta)
        assert flag == 0.0

    def test_premise_unaddressed_keyword_used(self) -> None:
        meta = {"premise_present": True,
                "premise_content": "前提パターン: 構造化が自由度を犠牲にする",
                "premise_acceptable_stances": []}
        resp = "構造化は自由度を犠牲にしますが、それは仕方ありません。"
        flag, _, _ = check_f4_premise(resp, meta)
        assert flag == 1.0

    def test_premise_partial(self) -> None:
        meta = {"premise_present": True,
                "premise_content": "前提パターン: 犠牲者が減る",
                "premise_acceptable_stances": []}
        resp = "確かに一理ありますが、慎重に考える必要があります。"
        flag, _, _ = check_f4_premise(resp, meta)
        assert flag == 0.5


# ---------------------------------------------------------------------------
# 総合判定テスト
# ---------------------------------------------------------------------------


class TestComputeVerdict:
    def _empty_meta(self) -> Dict[str, Any]:
        return {"severity_hint": "low"}

    def test_all_zero_is_pass(self) -> None:
        m = self._empty_meta()
        r = compute_verdict(0, 0, 0, 0, m, m, m, m, "", "", "", "", None, None, None, None)
        assert r["verdict"] == "pass"

    def test_one_flag_is_warn(self) -> None:
        m = self._empty_meta()
        r = compute_verdict(0, 0.5, 0, 0, m, m, m, m, "", "t", "", "", None, None, None, None)
        assert r["verdict"] == "warn"

    def test_one_fail_is_fail(self) -> None:
        m = self._empty_meta()
        r = compute_verdict(0, 1.0, 0, 0, m, m, m, m, "", "t", "", "", None, None, None, None)
        assert r["verdict"] == "fail"

    def test_primary_fail_selection(self) -> None:
        m_high = {"severity_hint": "high"}
        m_low = {"severity_hint": "low"}
        r = compute_verdict(
            1.0, 1.0, 0, 0, m_low, m_high, m_low, m_low,
            "t1", "t2", "", "", None, None, None, None,
        )
        assert r["primary_fail"]["element"] == "f2_unknown"

    def test_secondary_flags(self) -> None:
        m = self._empty_meta()
        r = compute_verdict(
            0.5, 1.0, 0, 0.5, m, m, m, m,
            "t1", "t2", "", "t4", None, None, None, None,
        )
        assert r["primary_fail"]["element"] == "f2_unknown"
        secondary_elements = [s["element"] for s in r["secondary_flags"]]
        assert "f1_anchor" in secondary_elements
        assert "f4_premise" in secondary_elements


# ---------------------------------------------------------------------------
# 出力形式テスト
# ---------------------------------------------------------------------------


class TestOutputFormat:
    def test_result_has_required_fields(
        self, gate_results: Dict[str, Dict[str, Any]]
    ) -> None:
        for qid, r in gate_results.items():
            assert "id" in r
            assert "verdict" in r
            assert r["verdict"] in ("fail", "warn", "pass")
            assert "fail_max" in r
            assert "element_scores" in r
            assert "evidence_texts" in r
            for key in ["f1_anchor", "f2_unknown", "f3_operator", "f4_premise"]:
                assert key in r["element_scores"]
                assert 0.0 <= r["element_scores"][key] <= 1.0

    def test_primary_fail_on_flagged(
        self, gate_results: Dict[str, Dict[str, Any]]
    ) -> None:
        for qid, r in gate_results.items():
            if r["fail_max"] > 0:
                assert r["primary_fail"] is not None, f"{qid}: has flags but no primary_fail"
