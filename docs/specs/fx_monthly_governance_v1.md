# FX Monthly Review / Feedback / Logic Audit Protocol v1

## 0. 目的

本 protocol は、FX Daily Protocol の月次運用ガバナンスを定義する。

`fx_monthly_review_v1.md`（月次分析の集計仕様）と `fx_observability_artifacts_v1.md`（日次〜週次の観測証跡仕様）の **上位レイヤ** として機能し、それらの出力を用いて「何を keep し、何を change candidate とし、どう version を上げるか」を決める運用ガバナンスを規定する。

本 protocol の役割は、予測を改善することそのものではなく、改善判断を監査可能な形で残すことである。

---

## 1. スコープ

### 1.1 扱うもの

1. **月次レビュー** — 既存 artifact を入力とした 6 軸の診断
2. **ロジック精査** — 月次で見直してよい項目と固定すべき項目の分離
3. **version 更新判断** — 更新タイミング・更新単位・変更記録・禁止事項

### 1.2 扱わないもの

- 日次 forecast 生成ロジック
- 日次 outcome / evaluation 生成ロジック
- 自動売買
- 自動キャリブレーション
- 任意日付での ad-hoc な仕様変更

---

## 2. 月次レビューの入力

月次レビューは、以下の既存 artifact を入力とする。新しい観測や新しい artifact は追加しない。

| # | artifact | 供給元 |
|---|----------|--------|
| 1 | `monthly_review.json` | fx_monthly_review_v1 |
| 2 | `monthly_review.md` | fx_monthly_review_v1 |
| 3 | `monthly_strategy_metrics.csv` | fx_monthly_review_v1 |
| 4 | `monthly_slice_metrics.csv` | fx_monthly_review_v1 |
| 5 | `monthly_review_flags.csv` | fx_monthly_review_v1 |
| 6 | `input_snapshot.json` | fx_observability_artifacts_v1 |
| 7 | `run_summary.json` | fx_observability_artifacts_v1 |
| 8 | `daily_report.md` | fx_observability_artifacts_v1 |
| 9 | `scoreboard.csv` | fx_observability_artifacts_v1 |
| 10 | `provider_health.csv` | fx_observability_artifacts_v1 |

---

## 3. 月次レビューの 6 軸観点

月次レビューでは、以下の 6 軸をこの順序で診断する。

### 3.1 基本成績

UGH と各 baseline の以下 metrics を確認する。

- forecast_count
- direction_hit_rate
- range_hit_rate
- mean_abs_close_error_bp
- mean_abs_magnitude_error_bp

### 3.2 baseline 差分

各 baseline に対する以下の差分を確認する。判定は差分表で行い、感想ではない。

- direction accuracy delta
- mean absolute close error delta
- mean absolute magnitude error delta

### 3.3 state / regime / event slice

UGH のみを対象とし、以下の slice 別成績を確認する。

- dominant_state
- regime_label
- volatility_label
- intervention_risk
- event_tag

### 3.4 disconfirmer / false positive

以下を確認する。

- direction_hit == false の代表例
- disconfirmer_explained == true の割合
- dominant_state / event_tag / regime_label ごとの false positive 集中

### 3.5 provider / observability

以下を確認する。

- missing windows
- provider lag rate
- fallback adjustment rate
- provider mix
- annotation coverage rate

### 3.6 review flags

既存 `fx_monthly_review_v1` が出す review flags を確認する。

flags は recommendation の材料だが自動採択しない。月次判断は human-reviewed governance とする。

---

## 4. 月次判断カテゴリ

月次レビューの結論を、以下の 4 カテゴリのいずれかに **必ず** 分類する。

| カテゴリ | 意味 | version 更新 |
|---|---|---|
| **Keep** | 現行ロジック維持 | しない |
| **Logic audit** | ロジック精査対象として次月検討へ送る | しない |
| **Data / provider remediation** | データ基盤・運用基盤の問題として扱う | しない |
| **Version promotion candidate** | 翌月の version 更新候補として採択 | する（theory / engine / schema / protocol のいずれか） |

### 4.1 各カテゴリの条件例

- **Keep** — baseline 差分が許容範囲内であり、slice 別成績にも偏りが見られない場合
- **Logic audit** — baseline に対して特定 slice で劣後が見られるが、provider / annotation に起因しない場合。次月に精査を行い、version 更新の要否を判断する
- **Data / provider remediation** — missing windows の集中、provider lag rate の悪化、annotation coverage rate の低下など、入力データ品質に起因する問題が確認された場合
- **Version promotion candidate** — Logic audit を経て原因が特定され、変更内容が明確であり、変更前後の影響範囲が限定できる場合

---

## 5. ロジック精査の対象制限

月次レビューは測定器を改造する場ではなく、測定器の較正を見直す場とする。

### 5.1 月次で見直してよいもの

- q_strength の係数
- alignment の重み
- grv_raw / grv_lock の係数
- state 閾値
- disconfirmer rule 設計
- X block の採否
- expected_range 生成ルール
- weekly / monthly aggregation の閾値

### 5.2 月次で固定すべきもの

- lifecycle state 定義
- core schema
- canonical business-day rule
- forecast fixed time
- forecast horizon
- baseline の種類
- ID / uniqueness rule
- fail-fast policy

---

## 6. version 更新ルール

### 6.1 更新タイミング

月次レビュー後にのみ許可する。営業日中・営業週中の変更は禁止する。

### 6.2 更新単位

変更は以下のいずれかに分類する。複数レイヤにまたがる場合は、レイヤごとに個別の version 更新として扱う。

| 更新単位 | 対象 |
|----------|------|
| theory_version | 理論・ロジック・係数の変更 |
| engine_version | 実行エンジン・パイプラインの変更 |
| schema_version | データスキーマ・artifact 構造の変更 |
| protocol_version | 運用ガバナンス・手順の変更 |

### 6.3 変更記録

version 更新時には、以下を必ず記録する。

- 変更理由
- 変更対象
- 変更前後の version
- baseline 比較への影響想定
- 再評価が必要な期間

### 6.4 禁止事項

- 変更内容不明のまま version を上げること
- 複数レイヤの変更を一括で混ぜること
- 月中に retroactive に旧 record を書き換えること

---

## 7. 月次レビューの出力

月次レビューの最終成果物として、以下の 3 つを生成する。

### 7.1 Monthly decision log

| フィールド | 説明 |
|-----------|------|
| review_month | レビュー対象月 |
| overall_judgment | Keep / Logic audit / Data/provider remediation / Version promotion candidate |
| key_flags | review flags のうち判断に影響したもの |
| baseline_comparison_summary | baseline 差分の要約 |
| logic_audit_candidates | ロジック精査候補のリスト |
| provider_annotation_concerns | provider / annotation に関する懸念事項 |
| final_recommendation | 最終的な recommendation |

### 7.2 Change candidate list

| フィールド | 説明 |
|-----------|------|
| candidate_id | 候補の識別子 |
| category | Keep / Logic audit / Data/provider remediation / Version promotion candidate |
| rationale | 候補とした理由 |
| expected_benefit | 期待される改善 |
| expected_risk | 想定されるリスク |
| owner | 担当 |
| status | 候補のステータス |

### 7.3 Version decision record

| フィールド | 説明 |
|-----------|------|
| update_performed | version 更新を実施したか (bool) |
| updated_versions | 更新した version の一覧 |
| unchanged_versions | 更新しなかった version の一覧 |
| freeze_window_start | 凍結期間の開始日 |
| freeze_window_end | 凍結期間の終了日 |
| rollback_trigger | rollback を発動する条件 |

---

## 8. 判断手順

毎月の実行順序を以下の 10 ステップで固定する。順序を崩してはならない。

1. 月次 artifact を確定する
2. strategy metrics を見る
3. baseline comparison を見る
4. state / regime / event slice を見る
5. false positive / disconfirmer を見る
6. provider / annotation 健全性を確認する
7. review flags を確認する
8. keep / audit / remediation / promotion candidate に分類する
9. version 更新可否を決定する
10. monthly decision log を確定する

順序固定の理由: 理論問題と運用問題を混同しないため。

---

## 9. 判定の優先順位

判断が衝突した場合の優先順位を以下の順で定める。

1. provider / missing window 問題
2. annotation coverage 問題
3. baseline に対する明確な劣後
4. state / disconfirmer の偏り
5. magnitude / range の改善余地

設計意図: 入力が壊れているのに理論をいじらない。

---

## 10. 完了条件

月次レビューは、以下をすべて満たしたときに完了とする。

- 6 軸観点（セクション 3）のすべてを確認した
- 判断手順（セクション 8）の 10 ステップをすべて実行した
- 月次判断カテゴリ（セクション 4）のいずれかに分類した
- Monthly decision log / Change candidate list / Version decision record の 3 成果物を生成した
- version 更新を行う場合、version 更新ルール（セクション 6）に従った記録を残した

---

## 11. 一文定義

> FX Monthly Review / Feedback / Logic Audit Protocol v1 とは、月次集計済み artifact 群を用いて、UGH と baseline の比較、state / event / provider / annotation の診断、変更候補の分類、version 更新可否の判断を、監査可能な順序と記録形式で行う governance protocol である。
