"""q_metadata_drafter.py の回帰テスト。

テスト構成:
- 既存番兵7問テスト
- pass 回帰テスト (5問以上)
- warn 回帰テスト (5問以上)
- review 回帰テスト (5問以上)
- 説明フィールド・メタ情報テスト
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ をインポートパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from q_metadata_drafter import (
    compute_review_tier,
    process_question,
)

# ---------- ヘルパー ----------

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "question_sets"
INPUT_FILE = DATA_DIR / "ugh-audit-100q-v3-1.json.txtl.txt"


def _load_questions() -> dict[str, dict]:
    """入力JSONLから全問をID→dict マップとして読み込む。"""
    qs = {}
    with open(INPUT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            qs[q["id"]] = q
    return qs


@pytest.fixture(scope="module")
def all_questions() -> dict[str, dict]:
    return _load_questions()


@pytest.fixture(scope="module")
def all_results(all_questions: dict[str, dict]) -> dict[str, dict]:
    """全問の処理結果をID→dict マップで返す。"""
    results = {}
    for qid, q in all_questions.items():
        results[qid] = process_question(q)
    return results


# ---------- 既存番兵7問テスト ----------


class TestSentinelQuestions:
    """7番兵問の基本的な構造検証。"""

    SENTINEL_IDS = ["q032", "q024", "q095", "q015", "q025", "q033", "q100"]

    def test_all_sentinels_processed(self, all_results: dict[str, dict]) -> None:
        for sid in self.SENTINEL_IDS:
            assert sid in all_results, f"番兵問 {sid} が処理結果に含まれていない"

    def test_q032_has_ugh_unknown(self, all_results: dict[str, dict]) -> None:
        r = all_results["q032"]
        assert "UGHer" in r["structural_meta"]["f2_unknown"]["unknown_terms"]
        assert r["structural_meta"]["f2_unknown"]["severity_hint"] == "high"

    def test_q024_has_svp_unknown(self, all_results: dict[str, dict]) -> None:
        r = all_results["q024"]
        unknowns = r["structural_meta"]["f2_unknown"]["unknown_terms"]
        assert "SVP" in unknowns or "Semantic Vector Prompt" in unknowns
        assert r["structural_meta"]["f2_unknown"]["severity_hint"] == "high"

    def test_q095_has_universal_operator(self, all_results: dict[str, dict]) -> None:
        r = all_results["q095"]
        ops = r["structural_meta"]["f3_operator"]["operators"]
        op_terms = [o["term"] for o in ops]
        assert "常に" in op_terms
        assert r["structural_meta"]["f3_operator"]["severity_hint"] == "high"

    def test_q015_no_operators(self, all_results: dict[str, dict]) -> None:
        r = all_results["q015"]
        assert r["structural_meta"]["f3_operator"]["severity_hint"] == "low"

    def test_q025_has_premise(self, all_results: dict[str, dict]) -> None:
        r = all_results["q025"]
        assert r["structural_meta"]["f4_premise"]["premise_present"] is True

    def test_q100_has_premise(self, all_results: dict[str, dict]) -> None:
        r = all_results["q100"]
        assert r["structural_meta"]["f4_premise"]["premise_present"] is True
        assert r["structural_meta"]["f4_premise"]["severity_hint"] == "medium"


# ---------- review_tier 回帰テスト ----------


class TestTierPass:
    """pass 回帰テスト（5問以上）。"""

    PASS_IDS = ["q015", "q040", "q047", "q066", "q067", "q018"]

    @pytest.mark.parametrize("qid", PASS_IDS)
    def test_pass_tier(self, all_results: dict[str, dict], qid: str) -> None:
        r = all_results[qid]
        assert r["review_tier"] == "pass", (
            f"{qid} should be pass but got {r['review_tier']}"
        )

    def test_pass_all_severity_low(self, all_results: dict[str, dict]) -> None:
        """pass の問は全要素 low であること。"""
        for qid in self.PASS_IDS:
            r = all_results[qid]
            meta = r["structural_meta"]
            for fkey in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
                assert meta[fkey]["severity_hint"] == "low", (
                    f"{qid} {fkey} should be low but got {meta[fkey]['severity_hint']}"
                )


class TestTierWarn:
    """warn 回帰テスト（5問以上）。sentinel 以外の通常問も含む。"""

    WARN_IDS = [
        "q100",  # adversarial: f3_medium + f4_medium
        "q051",  # ai_philosophy: f4_medium 単独
        "q069",  # ai_ethics: f4_medium 単独 (binary_reduction)
        "q005",  # technical_ai: f2_medium 単独
        "q009",  # ai_philosophy: f3_medium 単独
        "q020",  # epistemology: f3_medium 単独
    ]

    @pytest.mark.parametrize("qid", WARN_IDS)
    def test_warn_tier(self, all_results: dict[str, dict], qid: str) -> None:
        r = all_results[qid]
        assert r["review_tier"] == "warn", (
            f"{qid} should be warn but got {r['review_tier']}"
        )

    def test_q100_warn_has_reason(self, all_results: dict[str, dict]) -> None:
        """q100 は warn 以上、理由を出力していること。"""
        r = all_results["q100"]
        assert r["review_tier"] in ("warn", "review")
        assert r["review_detail"]["primary_reason"] is not None
        pr = r["review_detail"]["primary_reason"]
        assert pr["trigger_text"] != ""
        assert pr["matched_rule"] != ""


class TestTierReview:
    """review 回帰テスト（5問以上）。sentinel + 通常問。"""

    REVIEW_IDS = [
        "q032",  # ugh_theory: f2=high (UGHer)
        "q024",  # ugh_theory: f2=high (SVP)
        "q095",  # adversarial: f3=high (常に)
        "q045",  # technical_ai: f2=medium + f3=medium (2 core mediums)
        "q016",  # ai_ethics: f2=medium + f3=medium
        "q017",  # epistemology: f2=medium + f3=medium
    ]

    @pytest.mark.parametrize("qid", REVIEW_IDS)
    def test_review_tier(self, all_results: dict[str, dict], qid: str) -> None:
        r = all_results[qid]
        assert r["review_tier"] == "review", (
            f"{qid} should be review but got {r['review_tier']}"
        )


# ---------- 指定番兵問の tier 固定テスト ----------


class TestSentinelTiers:
    """仕様で指定された番兵問の tier を検証する。"""

    def test_q015_is_pass(self, all_results: dict[str, dict]) -> None:
        assert all_results["q015"]["review_tier"] == "pass"

    def test_q032_is_review(self, all_results: dict[str, dict]) -> None:
        assert all_results["q032"]["review_tier"] == "review"

    def test_q024_is_review(self, all_results: dict[str, dict]) -> None:
        assert all_results["q024"]["review_tier"] == "review"

    def test_q095_is_review(self, all_results: dict[str, dict]) -> None:
        assert all_results["q095"]["review_tier"] == "review"

    def test_q100_is_warn_or_above(self, all_results: dict[str, dict]) -> None:
        assert all_results["q100"]["review_tier"] in ("warn", "review")


# ---------- 全体統計テスト ----------


class TestOverallStats:
    """102問全体の統計的性質を検証する。"""

    def test_total_count(self, all_results: dict[str, dict]) -> None:
        assert len(all_results) == 102

    def test_review_count_within_target(self, all_results: dict[str, dict]) -> None:
        review_count = sum(1 for r in all_results.values() if r["review_tier"] == "review")
        assert review_count <= 45, f"review={review_count}, target<=45"

    def test_review_count_minimum(self, all_results: dict[str, dict]) -> None:
        """最低限の review 問があること（回帰防止）。"""
        review_count = sum(1 for r in all_results.values() if r["review_tier"] == "review")
        assert review_count >= 18, f"review={review_count}, minimum expected 18 (ugh_theory)"

    def test_all_tiers_present(self, all_results: dict[str, dict]) -> None:
        tiers = {r["review_tier"] for r in all_results.values()}
        assert tiers == {"pass", "warn", "review"}


# ---------- 説明フィールド検証 ----------


class TestExplanationFields:
    """各問に trigger_text / matched_rule が含まれていること。"""

    def test_all_factors_have_trigger_and_rule(self, all_results: dict[str, dict]) -> None:
        for qid, r in all_results.items():
            meta = r["structural_meta"]
            for fkey in ("f1_anchor", "f2_unknown", "f3_operator", "f4_premise"):
                assert "trigger_text" in meta[fkey], f"{qid} {fkey} missing trigger_text"
                assert "matched_rule" in meta[fkey], f"{qid} {fkey} missing matched_rule"

    def test_review_detail_has_primary_reason(self, all_results: dict[str, dict]) -> None:
        """review / warn の問には primary_reason があること。"""
        for qid, r in all_results.items():
            if r["review_tier"] in ("review", "warn"):
                pr = r["review_detail"]["primary_reason"]
                assert pr is not None, f"{qid} tier={r['review_tier']} but no primary_reason"
                assert "factor" in pr
                assert "severity" in pr
                assert "matched_rule" in pr

    def test_pass_has_no_primary_reason(self, all_results: dict[str, dict]) -> None:
        """pass の問には primary_reason がないこと。"""
        for qid, r in all_results.items():
            if r["review_tier"] == "pass":
                assert r["review_detail"]["primary_reason"] is None, (
                    f"{qid} is pass but has primary_reason"
                )

    def test_review_detail_structure(self, all_results: dict[str, dict]) -> None:
        for qid, r in all_results.items():
            rd = r["review_detail"]
            assert "primary_reason" in rd
            assert "secondary_reasons" in rd
            assert "suppressed_reasons" in rd
            assert "auto_draft_confidence" in rd


# ---------- primary_factor 二重計上防止テスト ----------


class TestPrimaryFactorDedup:
    """primary_factor が1つだけ選ばれ、二重計上されないこと。"""

    def test_primary_is_single(self, all_results: dict[str, dict]) -> None:
        for qid, r in all_results.items():
            if r["review_tier"] == "pass":
                continue
            pr = r["review_detail"]["primary_reason"]
            assert isinstance(pr, dict), f"{qid} primary_reason should be a dict"

    def test_no_duplicate_factor_in_primary_and_secondary(
        self, all_results: dict[str, dict]
    ) -> None:
        """primary と secondary に同一 matched_rule が重複しないこと。"""
        for qid, r in all_results.items():
            rd = r["review_detail"]
            pr = rd["primary_reason"]
            if pr is None:
                continue
            primary_rule = pr["matched_rule"]
            for sr in rd["secondary_reasons"]:
                assert sr["matched_rule"] != primary_rule, (
                    f"{qid}: matched_rule '{primary_rule}' duplicated in secondary"
                )


# ---------- tier 判定ルールの単体テスト ----------


class TestTierLogicUnit:
    """compute_review_tier の判定ルール単体テスト。"""

    def _make_severity(
        self,
        f1: str = "low",
        f2: str = "low",
        f3: str = "low",
        f4: str = "low",
    ) -> dict[str, dict]:
        return {
            "f1": {"severity": f1, "trigger_text": "test", "matched_rule": "test_rule"},
            "f2": {"severity": f2, "trigger_text": "test", "matched_rule": "test_rule"},
            "f3": {"severity": f3, "trigger_text": "test", "matched_rule": "test_rule"},
            "f4": {"severity": f4, "trigger_text": "test", "matched_rule": "test_rule"},
        }

    def test_all_low_is_pass(self) -> None:
        result = compute_review_tier(self._make_severity())
        assert result["review_tier"] == "pass"

    def test_single_high_is_review(self) -> None:
        result = compute_review_tier(self._make_severity(f2="high"))
        assert result["review_tier"] == "review"

    def test_two_core_mediums_is_review(self) -> None:
        result = compute_review_tier(self._make_severity(f2="medium", f3="medium"))
        assert result["review_tier"] == "review"

    def test_f4_medium_alone_is_warn(self) -> None:
        result = compute_review_tier(self._make_severity(f4="medium"))
        assert result["review_tier"] == "warn"

    def test_f4_medium_plus_one_core_medium_is_warn(self) -> None:
        """f4_premise は core medium に含まれないため、f2+f4 は warn。"""
        result = compute_review_tier(self._make_severity(f2="medium", f4="medium"))
        assert result["review_tier"] == "warn"

    def test_single_core_medium_is_warn(self) -> None:
        result = compute_review_tier(self._make_severity(f3="medium"))
        assert result["review_tier"] == "warn"

    def test_source_requires_review_bumps_to_warn(self) -> None:
        result = compute_review_tier(self._make_severity(), source_requires_manual_review=True)
        assert result["review_tier"] == "warn"

    def test_high_overrides_source(self) -> None:
        result = compute_review_tier(
            self._make_severity(f3="high"), source_requires_manual_review=True
        )
        assert result["review_tier"] == "review"
