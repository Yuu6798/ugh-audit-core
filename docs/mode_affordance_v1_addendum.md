# mode_affordance v1 追加仕様 (追いプロンプト)

mode_affordance v1 タスク仕様の実装中に追加確定した設計判断。
既存仕様と矛盾する場合はこちらを優先せよ。

---

## 1. runtime lookup 優先順位の明確化

既存仕様の「question_id 優先 → question exact match → not_available」を以下に置き換える:

```
canonical reviewed  >  inline explicit  >  not_available
```

### 詳細ルール

1. `question_id` が与えられ、canonical reviewed metadata (102問 JSONL) に存在する場合、その `mode_affordance` を使う
2. canonical にない `question_id`、または `question_id` なしの場合、リクエストに `question_meta.mode_affordance` が同梱されていればそれを使う
3. どちらも該当しない場合は `status="not_available"` を返す
4. **override ポリシー**: canonical reviewed に存在する question_id に対して、リクエストが別の `mode_affordance` を同梱していても、**デフォルトでは canonical を優先する**。override は明示フラグ（例: `mode_affordance_override=true`）がある場合のみ許可する

### 設計理由

- 監査エンジンは「中間に LLM を挟まない決定的計算」を設計原理とする
- mode_affordance の判定者は監査エンジン内部ではなく、質問メタデータの供給側である
- 102問は手動ラベルを正本化し、新規質問は入力時に mode_affordance を同梱して渡す方式が最も安定
- reviewed metadata を正本化する既存運用と整合する

---

## 2. f4 と response_mode_signal の方向性対比

背景設計セクションに追記すべき設計指針:

- **f4 (trap_type)** = 負方向の検出器。「罠に落ちたか」を測る。高スコア = 問題あり
- **response_mode_signal** = 正方向の検出器。「期待された答え方を満たしたか」を測る。高スコア = 良好

実装構造は f4 の trap 分岐と同系統（mode ごとに分岐して cue-list で検出）だが、
役割は逆。この対比を意識してスコアリングの極性を設計すること。

---

## 3. 非目標への追記

以下を非目標リストに追加:

- grv と response_mode_signal を 1 本のスコアに合成してはならない（Phase E で扱う）
- mode_conditioned_grv を v1 で実装してはならない
- response_mode_signal を grv の重み調整に使ってはならない

### 設計理由

grv は語彙の重力偏在を見る計測器、mode_affordance は答え方の型。
両者は直交しており、最初から 1 本のスコアに潰すと何が悪かったか読めなくなる。
v1 では grv_raw と response_mode_signal を別出力にし、
最終判定への合成は 48 件以上で人手較正してから行う（Phase E）。

---

## 4. Phase E 設計メモ（参考情報、v1 では実装しない）

以下は v1 のスコープ外だが、将来の Layer C 設計の方向性として記録する。
v1 実装時にこの構造を意識する必要はないが、v1 の出力が将来この方向に
拡張可能であることを阻害する設計は避けること。

### mode_conditioned_grv（将来構想）

grv との接続は「単純加算」ではなく「mode 条件付きの解釈ベクトル」にする。
4 成分:

| 成分 | 意味 |
|------|------|
| `anchor_alignment` | 高重力語が、問いの核語と mode に必要な語彙へ乗っているか |
| `balance` | 比較型・探索型で、重力が片側に潰れていないか |
| `boilerplate_risk` | 安全一般論・倫理一般論・回避表現に重力が吸われていないか |
| `collapse_risk` | 複数の論点や選択肢が要るのに 1 塊に潰れていないか |

### mode 別の読み方（将来構想）

| mode | 重要な成分 | 例 |
|------|-----------|-----|
| comparative | balance | q033: 「共振」だけに重力が寄って「相関」が空くと不適合 |
| critical | anchor_alignment + boilerplate_risk | q004: 「自己参照」「外部検証」に重力が乗るべき |
| exploratory | collapse_risk | q028: 一つの価値判断に潰れたら探索になっていない |
| action_required=true | anchor_alignment + boilerplate_risk | q065: 一般論ばかりで具体的運用設計に語彙が乗らないなら弱い |

### Layer C 合成方針（将来構想）

1. v1: grv_raw と response_mode_signal を別出力にする ← **今ここ**
2. v2: mode_conditioned_grv を説明用ベクトルとして追加
3. v3: 48件以上で人手較正した後、最終判定に混ぜる
