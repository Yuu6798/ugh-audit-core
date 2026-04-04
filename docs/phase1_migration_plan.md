# Phase 1 — UGH Audit Engine 移行計画

最終更新: 2026-03-23
対象: `ugh-audit-core` を起点とした UGH 関連リポジトリ全体

---

## 1. 前提認識

今回の更新は `ugh-audit-core` 単体の改善ではなく、UGH 関連リポジトリ全体を対象にした評価基盤の移行である。

新方針は、アップロードされた README に従い、既存の以下を縮退・撤去対象とする。

- embedding / cosine 類似ベース評価
- sentence-transformers 依存
- tfidf-char-ngram を中心とした暫定評価
- スコア計算と verdict が混ざった単純閾値ロジック
- モデル依存・推論依存の評価挙動

移行先は以下を満たす **UGH Audit Engine（意味の電卓）** である。

- 推論ゼロ
- 決定的
- モデル非依存
- 計算コスト計量化
- Goodhart 耐性を意識した構造

---

## 2. 現行コードベースの棚卸し結果

### 2.1 主要な現行依存

#### スコアリング中核
- `ugh_audit/scorer/ugh_scorer.py`
- `ugh_audit/scorer/models.py`

#### API / MCP 出力面
- `ugh_audit/server.py`
- `ugh_audit/mcp_server.py`

#### 永続化
- `ugh_audit/storage/audit_db.py`

#### レポート / 集計
- `ugh_audit/report/phase_map.py`

#### 収集ユーティリティ
- `ugh_audit/collector/audit_collector.py`

#### 参照定義
- `ugh_audit/reference/golden_store.py`

### 2.2 現行ロジックの特徴

#### `UGHScorer`
現在の scorer は三層フォールバック構成である。

1. `ugh3-metrics-lib`
2. `sentence-transformers`
3. `minimal`

この構造自体が新方針と衝突している。
理由:
- モデル依存
- 埋め込み依存
- 非決定的要素を含む
- 実装説明責任が弱い

#### `AuditResult`
現行 `AuditResult` の中心は以下:
- `por: float`
- `por_fired: bool`
- `delta_e`
- `delta_e_core`
- `delta_e_full`
- `delta_e_summary`
- `grv: Dict[str, float]`
- `meaning_drift` property

新方針では PoR は scalar ではなく **座標 `(S, C)`** になるため、ここは構造変更が必要。

#### verdict ロジック
現行 verdict は `AuditResult.meaning_drift` に埋め込まれている。

- `delta_e <= 0.04` → `同一意味圏`
- `delta_e <= 0.10` → `軽微なズレ`
- それ以外 → `意味乖離`

これは README 方針の
- `decision = f(delta_e_bin, C_bin)`
- `repair_order`
- `budget`

と不整合。

#### API 依存箇所
`server.py` / `mcp_server.py` はどちらも以下前提:
- `result.por`
- `result.delta_e`
- `result.grv`
- `result.meaning_drift`

新エンジン導入時に最も先に壊れるのはここ。

#### 永続化前提
`audit_db.py` は現行スキーマで以下を保存:
- scalar PoR
- `por_fired`
- `delta_e*`
- `grv_json`
- `meaning_drift`

新エンジンでは最低でも:
- evidence
- state
- policy
- budget

を保存対象に再設計する必要がある。

---

## 3. README 新設計とのギャップ

### 3.1 概念ギャップ

#### 現行
- PoR = 1 scalar
- ΔE = embedding距離 / 類似度差分寄り
- grv = 語彙分布の近似
- verdict = ΔE閾値

#### 新設計
- PoR = `(S, C)`
- S = 構造エラーの重み付きスコア
- C = core propositions coverage
- ΔE = `S, C` から計算される決定的距離
- grv = エントロピー + centroid 差
- verdict は state / bin / policy に基づく decision

### 3.2 実装ギャップ

#### 未実装で必要なもの
- f1〜f4 detector
- proposition matcher
- registry YAML 群
- calculator 層
- decision / policy / budget 層
- output schema

#### 既存で縮退対象
- ST モデルロード
- ugh3 backend 分岐
- cosine 類似度ベース PoR
- reference vs response 埋め込み距離ベース ΔE

---

## 4. 改修戦略

### 戦略原則

#### 原則1: 旧 scorer を即時上書きしない
新エンジンを新規 namespace に作り、比較可能性を残す。

#### 原則2: API は互換層を経由して段階移行
最初から既存レスポンスを壊さない。

#### 原則3: 先に calculator / schema を固定し、その後 detector を詰める
先に数式と state 形を固定しないと、detector 実装がブレる。

#### 原則4: verdict は property ではなく decision layer に切り出す
将来の calibration を見据え、ロジックを可視化する。

---

## 5. 実装フェーズ詳細

### Phase 1-A: 影響範囲固定（今回の成果）

#### 置換対象ファイル
- `ugh_audit/scorer/ugh_scorer.py`
- `ugh_audit/scorer/models.py`
- `ugh_audit/server.py`
- `ugh_audit/mcp_server.py`
- `ugh_audit/storage/audit_db.py`
- `ugh_audit/report/phase_map.py`
- `ugh_audit/collector/audit_collector.py`
- `ugh_audit/reference/golden_store.py`
- `README.md`
- `CLAUDE.md`
- 関連テスト群

#### 新設対象
- `ugh_audit/engine/`（新 namespace 推奨）
  - `detectors.py`
  - `calculator.py`
  - `decision.py`
  - `models.py`
  - `compat.py`
- `registry/`
  - `reserved_terms.yaml`
  - `operator_catalog.yaml`
  - `premise_frames.yaml`
- `opcodes/`
  - `metapatch_opcodes.yaml`
  - `runtime_repair_opcodes.yaml`
- `schema/output_schema.yaml`

### Phase 1-B: 互換仕様の策定

#### 新 engine 内部出力
想定 canonical output:
- `evidence`
- `state`
- `policy`
- `budget`

#### 互換変換で残す暫定旧キー
- `por`
- `delta_e`
- `grv`
- `verdict`

#### 互換ルール案
- `por` ← 互換上は `S` または `(S,C)` の縮約値を返すのではなく、暫定で `S` を返すか、APIのみ legacy field として維持する
- `delta_e` ← 新ΔE
- `grv` ← 新grv
- `verdict` ← decision layer の結果を legacy 名で投影

**注:** PoR を scalar のまま残すのは概念破壊を起こすので、将来的に deprecate 必須。

### Phase 2: Engine skeleton 新設

実装目標:
- 新 engine を旧 scorer から独立して import 可能にする
- README 数式の入れ物だけ先に確定

必要ファイル:
- `ugh_audit/engine/models.py`
- `ugh_audit/engine/calculator.py`
- `ugh_audit/engine/decision.py`
- `ugh_audit/engine/__init__.py`

### Phase 3: Detector 実装

実装対象:
- `f1_anchor`
- `f2_operator`
- `f3_reason_request`
- `f4_forbidden_reinterpret`
- proposition hit

依存資産:
- `q_metadata_structural_reviewed*.jsonl`
- `structural_gate_results.jsonl`
- questionごとの `core_propositions`

### Phase 4: API / MCP 接続

方針:
- `server.py` / `mcp_server.py` は engine adapter を呼ぶよう変更
- 旧レスポンスは互換層で提供
- 新 JSON は `debug` または `engine_output` キーで追加可能にする

### Phase 5: DB スキーマ移行

案:
- 旧テーブルを壊さず columns 追加
- 追加候補:
  - `por_s`
  - `por_c`
  - `evidence_json`
  - `state_json`
  - `policy_json`
  - `budget_json`
  - `decision`

### Phase 6: 検証 / calibration

必要データ:
- n=20 検証セット
- human score との比較
- 旧指標との再比較
- decision logic 一致確認

---

## 6. リスクと対策

### リスク1: proposition matcher 未整備で C が不安定
対策:
- 初期版は reviewed metadata の `original_core_propositions` をそのまま利用
- 後で専用 pattern compiler を追加

### リスク2: PoR の概念移行で downstream が壊れる
対策:
- API レベルでは暫定互換キーを残す
- DB では `por_s`, `por_c` を新設して移行

### リスク3: verdict の意味が変わる
対策:
- legacy verdict と engine decision を一定期間併記
- キャリブレーション用 CSV を再出力可能にする

### リスク4: grv の再定義が曖昧になる
対策:
- 旧 grv と新 grv を別名保存し、一時比較可能にする

### リスク5: Claude 側改修と衝突
対策:
- まずは Phase 1 文書化に止める
- 実装着手時は最新 remote branch 差分を読んでからマージ方針を決める

---

## 7. 次フェーズで着手する具体物

次に自分が実装着手するなら、順番は以下。

1. `ugh_audit/engine/` 骨格追加
2. engine 用 dataclass / typed dict 定義
3. calculator の純粋関数実装
4. decision layer の雛形実装
5. legacy adapter 作成

この段階ではまだ detector を完成させず、まず **新しい評価の器** を固定する。

---

## 8. 今回の Phase 1 の結論

- 今回の改修は scorer 差し替えではなく **評価基盤の再設計**。
- 最初に触るべき中心は `scorer/models/server/mcp/storage`。
- 新設計は既存 `AuditResult` 中心構造と整合しないため、**新 namespace 追加が安全**。
- API/DB は互換層を経由して段階移行すべき。
- 次着手は **engine skeleton 作成** が最適。
