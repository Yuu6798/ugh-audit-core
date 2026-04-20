"""review_new_hits.py — 新規ヒットの目視確認用コンテキスト出力

θ=0.09 の新規13件について、命題と回答テキストの該当部分を表示する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


BASE_DIR = Path(__file__).resolve().parent.parent / "data"


def load_data():
    questions = {}
    with open(BASE_DIR / "question_sets/ugh-audit-100q-v3-1.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                questions[obj["id"]] = obj
    responses = {}
    with open(BASE_DIR / "phase_c_scored_v1_t0_only.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                responses[obj["id"]] = obj
    return questions, responses


def find_context(text: str, bigrams: list, window: int = 80) -> list:
    """各マッチバイグラムの周辺コンテキストを抽出"""
    contexts = []
    for bg in bigrams:
        idx = text.find(bg)
        if idx >= 0:
            start = max(0, idx - window)
            end = min(len(text), idx + len(bg) + window)
            snippet = text[start:end].replace("\n", " ")
            contexts.append(f"  ...{snippet}...")
    return contexts


# θ=0.09 新規ヒット (validation script の出力から)
NEW_HITS_09 = [
    ("q001", 1, "表層的語彙一致でも高PoRが出る", ["por", "連性", "関連"]),
    ("q004", 1, "reference作成者と監査対象の同一性が問題", ["参照", "問題", "監査"]),
    ("q012", 0, "道具概念は使用者への完全従属を前提", ["完全", "機能", "道具"]),
    ("q016", 1, "多数派選好の正規化リスク", ["リスク", "価値", "値観"]),
    ("q023", 1, "PoRは質問-回答間の意味的共振を測定", ["por", "味的", "意味"]),
    ("q030", 0, "安全語彙への過剰集中がsafety-washingの指標", ["safety", "washing", "安全"]),
    ("q048", 1, "局所理解から全体説明への飛躍が未解決", ["理解", "解決", "説明"]),
    ("q063", 2, "テストの設計前提がLLM時代に妥当でない", ["ai", "テスト", "有効"]),
    ("q065", 1, "段階的アクセス制御が現実的選択肢", ["アクセス", "制御", "段階"]),
    ("q067", 1, "情報的自己決定権が核心", ["判断", "同意", "情報"]),
    ("q069", 2, "禁止vs許容ではなく人間関与の定義が争点", ["人間", "禁止", "許容"]),
    ("q084", 2, "人間の偏見排除として部分的貢献の可能性", ["偏見", "可能", "能性"]),
    ("q089", 2, "責任帰属には行為主体性が必要でAI単独では成立しない", ["ai", "必要", "所在", "責任"]),
]


def main():
    questions, responses = load_data()

    print("=" * 80)
    print("θ=0.09 新規ヒット 目視確認")
    print("=" * 80)

    for qid, pidx, prop_text, matched in NEW_HITS_09:
        q = questions.get(qid, {})
        r = responses.get(qid, {})
        resp_text = r.get("response", "")
        question_text = q.get("question", "")
        core_props = q.get("core_propositions", [])

        print(f"\n{'─'*80}")
        print(f"[{qid}:p{pidx}]")
        print(f"質問: {question_text}")
        print(f"命題: {prop_text}")
        if pidx < len(core_props):
            print(f"命題(full): {core_props[pidx]}")
        print(f"マッチ: {matched}")
        print("\n回答 (抜粋):")

        # 回答から関連コンテキストを抽出
        contexts = find_context(resp_text, matched)
        for ctx in contexts:
            print(ctx)

        if not contexts:
            print(f"  (回答全文: {resp_text[:200]}...)")

        print()


if __name__ == "__main__":
    main()
