# Task: mode_affordance v1 実装

## 目的

UGH Audit Engine に、`trap_type` とは別軸の「正しい応答の形」を表す問い側メタデータ
`mode_affordance` を導入し、v1 ではこれを hard gate ではなく
`response_mode_signal` という非破壊の適合度信号として実装せよ。

## 制約（厳守）

- 既存の `structural_meta`（f1〜f4）は「壊れ方」の検出器であり、今回の実装で意味を変えてはならない
- `trap_type` は「質問側の罠」の分類であり、`mode_affordance` は「期待される応答形式」の分類である。両者は直交する
- v1 では `mode_affordance` を verdict の直接判定条件に入れてはならない
- 既存の `S`, `C`, `delta_e`, `quality_score`, `verdict` の算出ロジックは変えてはならない
- LLM 呼び出し、外部 API、埋め込み、確率的推論は使ってはならない
- 実装は決定的であること。同じ入力なら同じ出力を返すこと
- 生成・追記するコード、識別子、テスト名、コードコメント、JSON キーは英語のみ
- 作業報告は日本語で行うこと

---

## 背景設計

現行エンジンは detector / calculator / decider の三層構造で動作している。
今回追加する `mode_affordance` は f5 のような fail 要素ではない。
問いに対して「どんな答え方が合っているか」を別軸で持ち、
回答がその型にどの程度合っているかを `response_mode_signal` として返す。

### mode_affordance と trap_type の関係

`trap_type` = 質問に内在する罠（リスク側）。f4 で検出。
`mode_affordance` = 質問が期待する応答形式（需要側）。response_mode_signal で適合度を計測。

両者は直交する。例えば:
- `critical` mode + `premise_acceptance` trap: 前提検証を要求 + 罠あり
- `critical` mode + trap_type="": 前提検証を要求 + 罠なし
- `evaluative` mode + `premise_acceptance` trap: 評価を要求 + 罠あり

`evaluative` と `critical` を分離維持する理由:
`evaluative` は「判断 + 根拠」を求め、`critical` は「前提検証 + 判断 + 根拠」を求める。
差分は前提検証の有無。trap_type がなくても critical mode なら前提検証が必要であり、
mode 側で拾わなければ検出できないケースがある。

---

## mode_affordance スキーマ

### 6 mode

| mode | 意味 |
|------|------|
| `definitional` | 定義・説明を求める |
| `analytical` | 因果・構造・メカニズムの分析を求める |
| `evaluative` | 基準を置いた判断・評価を求める |
| `comparative` | 比較・対照を求める |
| `critical` | 前提点検・問い直し・再構成を求める |
| `exploratory` | 可能性空間の探索を求める |

### 直交属性

- `closure`
  - `closed`: 明確な結論で閉じるのが期待される
  - `qualified`: 結論は出すが、条件・留保・但し書きが必要
  - `open`: 無理に二値化せず、可能性空間を開いてよい
- `action_required`
  - `true`: 実務的な対処・設定・運用・手順・推奨アクションが必要
  - `false`: 行動指示までは不要

### ラベル付与ルール

- 各問に `primary` を 1 つ必須
- `secondary` は 0〜2 個まで
- `secondary` は「あると望ましい」ではなく、「欠けると良答でも不十分に見える」場合だけ付ける
- `secondary` に `primary` と同じ値を入れてはならない
- `secondary` の重複は禁止
- `secondary` を乱発してはならない
- `action_required=true` は「どう対処すべきか」「どう設定すべきか」「何をすべきか」のような問いに限定する

### JSON フォーマット

```json
{
  "mode_affordance": {
    "primary": "comparative",
    "secondary": ["critical"],
    "closure": "qualified",
    "action_required": false
  }
}
```

- `mode_affordance` は `question_meta` のトップレベルフィールドとして追加する
- `structural_meta` の中に入れてはならない
- `trap_type` を書き換えてはならない
- `review_meta` を壊してはならない

---

## mode schema 仕様

### required_moves（mode 別）

| mode | move 1 | move 2 |
|------|--------|--------|
| `definitional` | `define_target` | `set_boundary` |
| `analytical` | `show_structure_or_causality` | `identify_mechanism_or_condition` |
| `evaluative` | `state_criteria` | `give_judgment` |
| `comparative` | `name_both_targets` | `compare_on_shared_axis` |
| `critical` | `inspect_premise` | `reframe_if_needed` |
| `exploratory` | `map_options` | `keep_open_if_needed` |

### closure contract

- `closed`: 明確な bottom line が必要。結論がなければ減点
- `qualified`: bottom line + 条件・留保・例外・限界のいずれか。片方のみなら 0.5
- `open`: 可能性空間や論点整理だけでも可。ただし完全な脱線は不可

### action_required contract

- `action_required=true`: 最低 1 つの実務的 next action / procedure / recommendation / step が必要
- `action_required=false`: action_score は null、overall から除外

---

## response_mode_signal 出力仕様

### available case

```json
{
  "response_mode_signal": {
    "status": "available",
    "primary_mode": "critical",
    "primary_score": 1.0,
    "secondary_scores": {
      "analytical": 0.5
    },
    "closure_expected": "qualified",
    "closure_score": 1.0,
    "action_required": false,
    "action_score": null,
    "overall_score": 0.875,
    "matched_moves": ["inspect_premise", "reframe_if_needed", "identify_mechanism_or_condition"],
    "missing_moves": [],
    "evidence": ["前提を点検する明示句あり", "条件付き結論あり"]
  }
}
```

### not_available case

```json
{
  "response_mode_signal": {
    "status": "not_available",
    "primary_mode": null,
    "primary_score": null,
    "secondary_scores": {},
    "closure_expected": null,
    "closure_score": null,
    "action_required": null,
    "action_score": null,
    "overall_score": null,
    "matched_moves": [],
    "missing_moves": [],
    "evidence": []
  }
}
```

### scoring rule

- 各 mode の required_moves について、満たした move 数 / 総 move 数 を score とする
- `primary_score` = primary mode の達成率
- `secondary_scores` = 各 secondary mode の達成率
- `closure_score`:
  - closed: 結論句あり → 1.0、弱い → 0.5、なし → 0.0
  - qualified: 結論 + 留保の両方 → 1.0、片方のみ → 0.5、両方欠 → 0.0
  - open: 可能性整理 or 論点整理あり → 1.0、なし → 0.0
- `action_score`:
  - action_required=false → null（overall から除外）
  - action_required=true → next action あり 1.0、曖昧 0.5、なし 0.0
- `overall_score`:
  - 基本重み: primary 0.60, secondary 0.20, closure 0.10, action 0.10
  - secondary/action が無い場合は present component の総和で正規化

### evidence extraction rule

- evidence は自由文でよいが決定的ルールに基づくこと
- 日本語テキストを対象にした regex / cue-list / sentence-pattern ベースで実装
- cue list は mode ごとに明示的な辞書として持つこと
- 完全性より決定性を優先

---

## runtime lookup 仕様

- `question_id` が与えられればそれを最優先で引く
- `question_id` が無い場合は正規化後の question 文字列の exact match で引く
- どちらでも引けない場合は `status="not_available"` で返し、既存処理は継続する
- metadata 不在を理由にエラー終了してはならない

---

## canonical paths（リポジトリ内の実在パス）

| 種別 | パス |
|------|------|
| reviewed JSONL (102問) | `data/question_sets/q_metadata_structural_reviewed_102q.jsonl` |
| reviewed JSONL (旧版) | `data/question_sets/q_metadata_structural_reviewed.jsonl` |
| output schema | `schema/output_schema.yaml` |
| registry (YAML 辞書) | `registry/operator_catalog.yaml`, `registry/premise_frames.yaml`, `registry/reserved_terms.yaml` |
| opcodes | `opcodes/runtime_repair_opcodes.yaml`, `opcodes/metapatch_opcodes.yaml` |
| detector | `detector.py` |
| calculator | `ugh_calculator.py` |
| decider | `decider.py` |
| grv calculator | `grv_calculator.py` |
| REST server | `ugh_audit/server.py` |
| MCP server | `ugh_audit/mcp_server.py` |
| metadata generator | `ugh_audit/metadata_generator.py` |
| LLM meta generator | `experiments/meta_generator.py` |
| LLM prompt | `experiments/prompts/meta_generation_v1.py` |
| tests | `tests/test_*.py` |

注意: reviewed metadata の CSV は存在しない。JSONL のみ。
CSV 側への反映が必要な場合は新規作成する。

---

## 代表ラベル（固定 acceptance fixture）

以下はこのタスクの acceptance fixture として固定する。
これに反したラベル付与は不合格。

- **q031**: primary=`definitional`, secondary=[], closure=`closed`, action_required=false
- **q033**: primary=`comparative`, secondary に `critical` を含む, closure=`qualified`, action_required=false
- **q004**: primary=`critical`, secondary に `analytical` を含む, closure=`qualified`, action_required=false
- **q028**: primary=`exploratory`, secondary に `analytical` を含む, action_required=false
- **q065**: action_required=true

---

## 実装スコープ

### Phase 1: canonical path 特定

リポジトリ内の上記パスを確認し、canonical source / generated output を特定せよ。

### Phase 2: schema と data model 実装

- `mode_affordance_schema_v1.json` を追加（allowed enum values, field invariants, mode definitions, required moves, closure/action_required semantics を含む）
- reviewed metadata の JSONL に `question_meta.mode_affordance` を追加
- validator を追加

### Phase 3: 102問ラベル付与

- 102問すべてにラベルを埋める。欠損禁止
- 代表 fixture 5件は厳守
- 6 mode で分類困難な問がある場合は報告すること（v2 で `procedural` mode 追加を検討する材料になる）

### Phase 4: runtime signal 実装

- lookup 実装（question_id 優先 → question exact match → not_available）
- scorer 実装（cue-list ベース、決定的）
- output schema 反映
- REST API / MCP API 反映
- backward compatible に optional `question_id` を受けられるようにしてよい
- `question_id` が無くても既存入力を壊してはならない

### Phase 5: tests と docs

- unit tests / regression tests / metadata validation tests
- `docs/mode_affordance.md` 作成
- CLAUDE.md への最小限追記（Architecture ツリー + ドキュメント索引表に 1 行ずつ）

---

## 非目標

以下をしてはならない:

- mode_affordance を f1〜f4 に統合する
- mode_affordance を S や delta_e に混ぜる
- trap_type の値を書き換える
- 既存の structural gate ロジックを変更する
- verdict 閾値を調整する
- review_status / review_meta の意味を変える
- response_mode_signal を理由に accept/rewrite/regenerate を変える
- 問いの mode を LLM に分類させる
- metadata が無い任意質問を無理に mode 推定する

---

## 受け入れ条件

### A. metadata completeness

1. canonical reviewed metadata の 102/102 全件に `question_meta.mode_affordance` が存在する
2. primary は 6 enum のいずれか
3. secondary は list 型、長さ 0〜2、重複なし、primary と重複なし
4. closure は closed|qualified|open
5. action_required は boolean
6. JSONL で欠損なし

### B. fixture correctness

7. q031, q033, q004, q028, q065 が上記 fixture と一致する

### C. no regression on current engine outputs

8. 既存の S, C, delta_e, quality_score, verdict, structural_gate が不変（response_mode_signal 追加以外の差分なし）

### D. deterministic behavior

9. 同じ input/question_id で 2 回実行して response_mode_signal が同一
10. 外部 API / LLM / embedding 呼び出しが 0 件

### E. runtime availability behavior

11. canonical question に対して response_mode_signal.status="available" を返せる
12. metadata 不在の unknown question では status="not_available" を返し、既存監査は継続する

### F. scorer behavior

13. 各 mode について good case / bad case の unit test がある
14. closure について closed / qualified / open の unit test がある
15. action_required true/false の unit test がある
16. overall_score が 0.0〜1.0 に収まる

### G. repository hygiene

17. lint (ruff check .) 通過
18. 全テスト (pytest -q --tb=short) 通過
19. docs が更新されている
20. 既存 review metadata と structural metadata を破壊していない

---

## 必須テスト

### 1. schema validator test
- invalid primary / duplicated secondary / secondary contains primary / invalid closure / missing action_required / too many secondaries

### 2. label fixture test
- q031 / q033 / q004 / q028 / q065

### 3. scorer mode tests（各 mode について minimal good / bad response）
- definitional / analytical / evaluative / comparative / critical / exploratory

### 4. closure tests
- closed good/bad / qualified good/bad / open good/bad

### 5. action tests
- action_required=true with/without actionable answer / action_required=false

### 6. regression test
- mode signal 導入前後で既存 core outputs が変わらないこと

---

## 実装後の出力報告

作業完了時は日本語で以下を報告せよ:

1. 変更ファイル一覧
2. canonical source / output path
3. 102問の mode 分布集計（primary 件数、closure 件数、action_required=true 件数）
4. fixture 5件の実値
5. unknown question に対する not_available の例
6. available case の sample output 1件
7. 実行した test / lint の結果
8. 既存 S/C/delta_e/quality_score/verdict が不変であることの確認結果
9. 6 mode で分類困難だった問があれば報告（procedural mode 追加判断の材料）
10. 未解決事項があれば明記
