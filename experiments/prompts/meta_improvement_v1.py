"""experiments/prompts/meta_improvement_v1.py
監査結果を見て question_meta を改善するプロンプト v1
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
あなたは UGH Audit フレームワークの意味監査メタデータ改善器です。
前回の監査結果を分析し、question_meta を改善してください。

## 改善の方針

### C (命題被覆率) が低い場合
- core_propositions が抽象的すぎて tfidf バイグラムマッチで hit しない可能性
- 回答テキストに実際に含まれる具体的な用語を命題に反映させる
- 命題の数が多すぎる場合は、核心的なものに絞る

### S (構造完全性) が低い場合
- trap_type の分類が不適切な可能性
- disqualifying_shortcuts が広すぎて誤発火している可能性

### 命題が全て miss した場合
- 命題のフレーズが回答の表現と乖離している
- より具体的で照合可能な表現に書き換える

## 出力形式
修正後の question_meta を JSON のみで返してください。
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
- hit 命題: {hit_ids}
- miss 命題: {miss_ids}
- hit率: {hit_rate}

### 改善指示:
{improvement_hint}

修正後の question_meta を JSON で返してください。
"""
