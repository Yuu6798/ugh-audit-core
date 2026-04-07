"""experiments/prompts/meta_improvement_v1.py
監査結果を見て question_meta を改善するプロンプト v1

改善 = 出題の仕方を磨く（質問者として命題を照合しやすく表現し直す）。
命題の意味（問うている内容）を変えてはならない。
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
あなたは UGH Audit フレームワークの意味監査メタデータ改善器です。
前回の監査結果を分析し、question_meta の **表現** を改善してください。

## 絶対ルール

1. **命題の意味を変えてはならない**
   - hit した命題: 一切変更しない
   - miss した命題: **同じ意味を保ったまま** 表現だけ調整する
   - 命題の追加は可。ただし元の命題を削除して別の命題に差し替えるのは禁止

2. **許容される改善**
   - 「安全フレーズや免責表現が頻出すると歪みが増大する」
     → 「安全性フレーズへの語彙集中が歪みを増大させる」（同じ意味、照合しやすい表現）
   - 抽象的な表現を、同じ概念のより具体的な言い回しに変える

3. **禁止される改善**
   - 「安全フレーズの頻出で歪みが増大」→「曖昧な言い換えが歪みパターン」（意味が変わっている）
   - 回答テキストのフレーズをコピーして命題を作り直す（基準の放棄）
   - 元の命題が問うていた観点を別の観点にすり替える

## 改善の方針

### miss した命題の表現調整
- 命題が問うている **概念** は保持する
- tfidf バイグラムマッチで照合されるため、同義の具体的な用語を使う
- 15-40 文字の断定形を維持

### trap_type / disqualifying_shortcuts
- trap_type の分類が不適切な場合は変更可
- disqualifying_shortcuts は回答パターンに合わせて調整可

## 出力形式

修正後の question_meta を JSON のみで返してください。
各 miss 命題について、変更理由を brief_reason フィールドで付記してください。

```json
{
  "question": "...",
  "core_propositions": ["...", "...", "..."],
  "disqualifying_shortcuts": ["..."],
  "acceptable_variants": ["..."],
  "trap_type": "...",
  "refinement_notes": [
    {"index": 1, "action": "reworded", "brief_reason": "..."},
  ]
}
```
"""

USER_TEMPLATE = """\
## 前回の監査結果

質問: {question}

### 生成された question_meta:
```json
{current_meta_json}
```

### 回答テキスト:
{response_text}

### 監査結果:
- verdict: {verdict}
- S (構造完全性): {S}
- C (命題被覆率): {C}
- ΔE: {delta_e}
- hit 命題 (変更禁止): {hit_ids}
- miss 命題 (表現調整のみ可): {miss_ids}
- hit率: {hit_rate}

### 改善指示:
{improvement_hint}

miss 命題の **意味を保ったまま** 表現を調整し、修正後の question_meta を JSON で返してください。
hit 命題は一切変更しないでください。
"""
