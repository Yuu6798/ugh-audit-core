"""
examples/basic_audit.py
基本的な使用例
"""
from ugh_audit import UGHScorer, AuditDB, GoldenStore
from ugh_audit.report.phase_map import generate_text_report

def main():
    scorer = UGHScorer(model_id="claw-v1")
    db = AuditDB()
    golden = GoldenStore()

    # テストケース: UGH理論の核心的な問い
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
        {
            "question": "ΔEが0.12のとき何を意味しますか？",
            "response": (
                "ΔEが0.12は意味的乖離を示します。"
                "仕様書の定義では0.10以上は別コンセプトとされるため、"
                "この回答は意図した意味から大きく逸脱していると判断されます。"
            ),
        },
    ]

    print("🔍 UGH Audit Core - 基本テスト実行\n")

    results = []
    for i, case in enumerate(test_cases, 1):
        result = scorer.score(
            question=case["question"],
            response=case["response"],
            reference=case.get("reference"),
            session_id="example-session-01",
        )
        db.save(result)
        results.append(result)
        print(f"[{i}] Q: {case['question'][:30]}...")
        print(f"    {result}")
        print()

    # Phase Mapレポート
    history = db.drift_history(limit=50)
    print(generate_text_report(history))

    # セッションサマリー
    summary = db.session_summary("example-session-01")
    print(f"\n📋 セッションサマリー: {summary}")


if __name__ == "__main__":
    main()
