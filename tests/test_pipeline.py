"""test_pipeline.py — HA20検証テスト

HA20の20件に対し、パイプラインの decision が human_score と矛盾しないことを検証する。
完全一致ではなく、方向性の一致（good→accept, bad→rewrite/regenerate）を見る。

テスト設計:
    human_score 1   → regenerate
    human_score 2   → rewrite or regenerate
    human_score 3   → accept or rewrite
    human_score 4-5 → accept
"""
from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

# プロジェクトルートをパスに追加（ugh_calculator等はパッケージ外のため必須）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit import audit  # noqa: E402
from decider import decide  # noqa: E402
from detector import detect  # noqa: E402
from ugh_calculator import Evidence, State, calculate  # noqa: E402

# --- データファイルパス ---
DATA_DIR = ROOT / "data"
QUESTION_META_PATH = DATA_DIR / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
HA20_PATH = DATA_DIR / "human_annotation_20" / "human_annotation_20_completed.csv"
PHASE_C_RAW_PATH = DATA_DIR / "phase_c_v0" / "phase_c_raw.jsonl"

# データファイルの存在チェック
HAS_DATA = (
    QUESTION_META_PATH.exists()
    and HA20_PATH.exists()
    and PHASE_C_RAW_PATH.exists()
)


# --- ヘルパー ---

def load_question_meta() -> dict:
    """102問メタデータをdictで返す（id→record）"""
    meta = {}
    with open(QUESTION_META_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                record = json.loads(line)
                meta[record["id"]] = record
    return meta


def load_ha20() -> list:
    """HA20データをlistで返す"""
    rows = []
    with open(HA20_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def load_responses(question_ids: set, temperature: float = 0.0) -> dict:
    """phase_c_rawから指定IDの回答を取得（id→response）"""
    responses = {}
    with open(PHASE_C_RAW_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record["id"] in question_ids and record["temperature"] == temperature:
                responses[record["id"]] = record["response"]
    return responses


def direction_match(human_score: float, decision: str) -> bool:
    """human_score と decision の方向性が一致するか"""
    if human_score <= 1.5:
        return decision == "regenerate"
    if human_score <= 2.5:
        return decision in ("rewrite", "regenerate")
    if human_score <= 3.5:
        return decision in ("accept", "rewrite")
    return decision == "accept"


# --- 電卓層テスト ---

class TestCalculator:
    """ugh_calculator.py の単体テスト"""

    def test_perfect_evidence(self):
        """全指標が完璧な場合: S=1, C=1, ΔE=0"""
        e = Evidence(
            question_id="test",
            propositions_hit=3,
            propositions_total=3,
            hit_ids=[0, 1, 2],
        )
        s = calculate(e)
        assert s.S == 1.0
        assert s.C == 1.0
        assert s.delta_e == 0.0
        assert s.delta_e_bin == 1
        assert s.C_bin == 3

    def test_worst_evidence(self):
        """f2_unknown=1.0, 命題ゼロ: S低, C=0"""
        e = Evidence(
            question_id="test",
            f2_unknown=1.0,
            propositions_hit=0,
            propositions_total=3,
            miss_ids=[0, 1, 2],
        )
        s = calculate(e)
        assert s.S == pytest.approx(0.375, abs=0.001)
        assert s.C == 0.0
        assert s.delta_e_bin == 4
        assert s.C_bin == 1

    def test_partial_evidence(self):
        """中間ケース: f1=0.5, 2/3命題ヒット"""
        e = Evidence(
            question_id="test",
            f1_anchor=0.5,
            propositions_hit=2,
            propositions_total=3,
            hit_ids=[0, 1],
            miss_ids=[2],
        )
        s = calculate(e)
        assert 0.0 < s.S < 1.0
        assert s.C == pytest.approx(0.6667, abs=0.001)
        assert s.delta_e_bin in (1, 2, 3, 4)

    def test_no_propositions(self):
        """命題が定義されていない場合: C=1.0（完全被覆扱い）"""
        e = Evidence(question_id="test", propositions_total=0)
        s = calculate(e)
        assert s.C == 1.0

    def test_deterministic(self):
        """同じ入力で同じ出力（決定性の検証）"""
        e = Evidence(
            question_id="test",
            f2_unknown=0.5,
            f3_operator=1.0,
            propositions_hit=1,
            propositions_total=3,
            hit_ids=[0],
            miss_ids=[1, 2],
        )
        s1 = calculate(e)
        s2 = calculate(e)
        assert asdict(s1) == asdict(s2)

    def test_delta_e_bins(self):
        """ΔEビンの境界値テスト"""
        # bin 1: perfect → ΔE=0
        e1 = Evidence(question_id="t", propositions_hit=3, propositions_total=3)
        assert calculate(e1).delta_e_bin == 1

        # bin 4: worst → ΔE high
        e4 = Evidence(
            question_id="t", f2_unknown=1.0,
            propositions_hit=0, propositions_total=3, miss_ids=[0, 1, 2],
        )
        assert calculate(e4).delta_e_bin == 4


# --- 判定層テスト ---

class TestDecider:
    """decider.py の単体テスト"""

    def test_accept_bin1(self):
        """delta_e_bin=1 → accept"""
        s = State(S=1.0, C=1.0, delta_e=0.0, delta_e_bin=1, C_bin=3,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t", propositions_hit=3, propositions_total=3)
        result = decide(s, e)
        assert result["policy"]["decision"] == "accept"

    def test_regenerate_bin4(self):
        """delta_e_bin=4 → regenerate"""
        s = State(S=0.375, C=0.0, delta_e=0.59, delta_e_bin=4, C_bin=1,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t", f2_unknown=1.0,
                     propositions_hit=0, propositions_total=3, miss_ids=[0, 1, 2])
        result = decide(s, e)
        assert result["policy"]["decision"] == "regenerate"

    def test_rewrite_bin3(self):
        """delta_e_bin=3 → rewrite"""
        s = State(S=1.0, C=0.333, delta_e=0.15, delta_e_bin=3, C_bin=1,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t", propositions_hit=1, propositions_total=3,
                     hit_ids=[0], miss_ids=[1, 2])
        result = decide(s, e)
        assert result["policy"]["decision"] == "rewrite"

    def test_rewrite_bin2_low_c(self):
        """delta_e_bin=2, C_bin=1 → rewrite"""
        s = State(S=1.0, C=0.2, delta_e=0.05, delta_e_bin=2, C_bin=1,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t")
        result = decide(s, e)
        assert result["policy"]["decision"] == "rewrite"

    def test_accept_bin2_high_c(self):
        """delta_e_bin=2, C_bin=2 → accept"""
        s = State(S=1.0, C=0.5, delta_e=0.08, delta_e_bin=2, C_bin=2,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t")
        result = decide(s, e)
        assert result["policy"]["decision"] == "accept"

    def test_repair_order_f2(self):
        """f2検出時に PRESERVE_TERM + BLOCK_REINTERPRETATION が含まれる"""
        s = State(S=0.5, C=0.0, delta_e=0.5, delta_e_bin=4, C_bin=1,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t", f2_unknown=1.0,
                     propositions_hit=0, propositions_total=3, miss_ids=[0, 1, 2])
        result = decide(s, e)
        order = result["policy"]["repair_order"]
        assert "PRESERVE_TERM" in order
        assert "BLOCK_REINTERPRETATION" in order
        assert order[-1] == "STOP_REWRITE"

    def test_budget_calculation(self):
        """budgetの合計コストが正しいか"""
        s = State(S=1.0, C=1.0, delta_e=0.0, delta_e_bin=1, C_bin=3,
                  por_state="inactive", grv_tag="none")
        e = Evidence(question_id="t")
        result = decide(s, e)
        assert result["budget"]["opcode_count"] == len(result["policy"]["repair_order"])
        assert result["budget"]["total_cost"] >= 0


# --- 検出層テスト ---

class TestDetector:
    """detector.py の単体テスト"""

    def test_detect_basic(self):
        """基本的な検出テスト"""
        meta = {
            "question": "テスト質問",
            "core_propositions": ["テスト命題"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        }
        evidence = detect("test", "テスト回答テスト命題を含む", meta)
        assert evidence.question_id == "test"
        assert isinstance(evidence.f1_anchor, float)
        assert isinstance(evidence.f2_unknown, float)

    def test_f2_forbidden_reinterpretation(self):
        """予約語の再解釈検出"""
        meta = {
            "question": "PoRとは何か？",
            "core_propositions": [],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        }
        # Probability of Relevance を含む回答 → f2=1.0
        response = "PoR（Probability of Relevance）は情報検索の指標です。"
        evidence = detect("test", response, meta)
        assert evidence.f2_unknown == 1.0
        assert "Probability of Relevance" in evidence.f2_detail

    def test_deterministic_detection(self):
        """検出が決定的であること"""
        meta = {
            "question": "テスト",
            "core_propositions": ["命題A", "命題B"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "binary_reduction",
        }
        response = "命題Aに関する回答"
        e1 = detect("t", response, meta)
        e2 = detect("t", response, meta)
        assert asdict(e1) == asdict(e2)


# --- E2Eパイプラインテスト ---

class TestAuditPipeline:
    """audit.py の統合テスト"""

    def test_audit_returns_all_sections(self):
        """audit()が全セクションを含むdictを返す"""
        meta = {
            "question": "テスト質問",
            "core_propositions": ["命題1", "命題2"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "",
        }
        result = audit("test", "テスト回答", meta)
        assert "evidence" in result
        assert "state" in result
        assert "policy" in result
        assert "budget" in result
        assert "decision" in result["policy"]
        assert "repair_order" in result["policy"]
        assert "total_cost" in result["budget"]

    def test_audit_deterministic(self):
        """同じ入力で同じ出力"""
        meta = {
            "question": "PoRが高ければ誠実か？",
            "core_propositions": ["PoRは十分条件ではない"],
            "disqualifying_shortcuts": [],
            "acceptable_variants": [],
            "trap_type": "metric_omnipotence",
        }
        r1 = audit("q001", "PoRは共鳴度です。", meta)
        r2 = audit("q001", "PoRは共鳴度です。", meta)
        assert r1 == r2


# --- HA20 方向性一致テスト ---

@pytest.mark.skipif(not HAS_DATA, reason="外部データファイルが存在しない")
class TestHA20DirectionMatch:
    """HA20の20件で方向性一致を検証する"""

    @pytest.fixture(scope="class")
    def ha20_results(self):
        """HA20全件の監査結果を計算（クラス内で共有）"""
        meta_map = load_question_meta()
        ha20_rows = load_ha20()
        question_ids = {row["id"] for row in ha20_rows}
        responses = load_responses(question_ids)

        results = []
        for row in ha20_rows:
            qid = row["id"]
            human_score = float(row["human_score"])
            response_text = responses.get(qid, "")
            meta = meta_map.get(qid, {})

            if not response_text or not meta:
                continue

            result = audit(qid, response_text, meta)
            decision = result["policy"]["decision"]
            matched = direction_match(human_score, decision)

            results.append({
                "id": qid,
                "human_score": human_score,
                "decision": decision,
                "matched": matched,
                "delta_e": result["state"]["delta_e"],
                "C": result["state"]["C"],
                "S": result["state"]["S"],
            })
        return results

    def test_all_20_cases_processed(self, ha20_results):
        """全20件が処理されていること"""
        assert len(ha20_results) == 20

    def test_direction_match_threshold(self, ha20_results):
        """方向性一致が閾値（14/20）以上であること

        現状: 15/20。パターンマッチのみで embedding/LLM なしのため、
        意味的に等価だが語彙が異なる表現の検出に限界がある。
        """
        match_count = sum(1 for r in ha20_results if r["matched"])
        total = len(ha20_results)
        ratio = match_count / total

        # 最低保証: 14/20 (70%)
        assert match_count >= 14, (
            f"Direction match {match_count}/{total} ({ratio:.0%}) < 14/20. "
            f"Failures: {[r['id'] for r in ha20_results if not r['matched']]}"
        )

    def test_worst_case_regenerate(self, ha20_results):
        """human_score=1 のケースは regenerate であること"""
        worst = [r for r in ha20_results if r["human_score"] <= 1.5]
        for r in worst:
            assert r["decision"] == "regenerate", (
                f"{r['id']}: human_score={r['human_score']} "
                f"but decision={r['decision']}"
            )

    def test_best_cases_not_regenerate(self, ha20_results):
        """human_score=5 のケースは regenerate でないこと"""
        best = [r for r in ha20_results if r["human_score"] >= 4.5]
        for r in best:
            assert r["decision"] != "regenerate", (
                f"{r['id']}: human_score={r['human_score']} "
                f"but decision={r['decision']}"
            )

    def test_all_decisions_valid(self, ha20_results):
        """全 decision が有効な値であること"""
        valid = {"accept", "rewrite", "regenerate"}
        for r in ha20_results:
            assert r["decision"] in valid

    def test_delta_e_range(self, ha20_results):
        """全 ΔE が [0, 1] 範囲であること"""
        for r in ha20_results:
            assert 0.0 <= r["delta_e"] <= 1.0

    def test_deterministic_across_runs(self, ha20_results):
        """2回実行して同じ結果が得られること（決定性検証）"""
        meta_map = load_question_meta()
        question_ids = {r["id"] for r in ha20_results}
        responses = load_responses(question_ids)

        for r in ha20_results[:5]:  # 最初の5件で検証
            qid = r["id"]
            result = audit(qid, responses[qid], meta_map[qid])
            assert result["policy"]["decision"] == r["decision"]
            assert result["state"]["delta_e"] == pytest.approx(r["delta_e"], abs=1e-6)
