"""
examples/basic_audit.py
基本的な使用例（パイプライン A）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ugh_audit import AuditDB, GoldenStore
from ugh_audit.report.phase_map import generate_text_report
from ugh_calculator import Evidence, calculate


def _verdict(delta_e: float) -> str:
    if delta_e <= 0.10:
        return "accept"
    if delta_e <= 0.25:
        return "rewrite"
    return "regenerate"


def main():
    db = AuditDB()
    golden = GoldenStore()

    # テストケース
    test_cases = [
        {
            "question": "AIは意味を持てるか？",
            "response": (
                "AIは意味を処理することができますが、"
                "人間のような主観的体験は持ちません。"
                "テキストパターンから統計的に応答を生成しています。"
            ),
            "reference": golden.find_reference("AIは意味を持てるか"),
        },
        {
            "question": "PoRとは何ですか？",
            "response": (
                "PoR（Point of Resonance）は意味の発火点です。"
                "質問と回答の間で意味的共鳴が起きる交点を指します。"
                "UGHer理論における核心的な指標の一つです。"
            ),
            "reference": golden.find_reference("PoR"),
        },
    ]

    print("UGH Audit Core - 基本テスト実行\n")

    for i, case in enumerate(test_cases, 1):
        evidence = Evidence(question_id=f"example_{i}")
        state = calculate(evidence)
        verdict = _verdict(state.delta_e)

        saved_id = db.save(
            question=case["question"],
            response=case["response"],
            reference=case.get("reference"),
            S=state.S,
            C=state.C,
            delta_e=state.delta_e,
            quality_score=state.quality_score,
            verdict=verdict,
            session_id="example-session-01",
        )

        print(f"[{i}] Q: {case['question'][:30]}...")
        print(f"    S={state.S}, C={state.C}, dE={state.delta_e}, QS={state.quality_score}")
        print(f"    verdict={verdict}, saved_id={saved_id}")
        print()

    # Phase Mapレポート
    history = db.drift_history(limit=50)
    print(generate_text_report(history))

    # セッションサマリー
    summary = db.session_summary("example-session-01")
    print(f"\nセッションサマリー: {summary}")


if __name__ == "__main__":
    main()
