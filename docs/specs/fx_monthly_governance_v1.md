# FX Monthly Review / Feedback / Logic Audit Protocol v1

## 0. 目的

本 protocol は、FX Daily Protocol の月次運用ガバナンスを定義する。

`fx_monthly_review_v1.md`（月次分析の集計仕様）と `fx_observability_artifacts_v1.md`（日次〜週次の観測証跡仕様）の **上位レイヤ** として機能し、それらの出力を用いて「何を keep し、何を change candidate とし、どう version を上げるか」を決める運用ガバナンスを規定する。

本 protocol の役割は、予測を改善することそのものではなく、改善判断を監査可能な形で残すことである。

### 0.1 自動化原則

本 protocol は、フィードバック（集計・診断・記録生成）と監査（6軸確認・flags 評価・異常検出）を **自動実行** する。これらは既存ロジックの観測・記録であり、ロジック変更を伴わないためである。

ロジック変更を伴う判断（カテゴリ分類の最終承認・version 更新の決定）のみ human-reviewed とする。

| 区分 | 自動化 | 理由 |
|------|--------|------|
| artifact 収集・検証 | **自動** | 既存 artifact の読み取りのみ |
| 6 軸診断の実行 | **自動** | metrics の集計・比較のみ |
| review flags の評価 | **自動** | 既存ルールに基づく判定のみ |
| 異常・劣後の検出 | **自動** | 差分計算・閾値照合のみ |
| decision log ドラフト生成 | **自動** | 診断結果の構造化出力のみ |
| change candidate list ドラフト生成 | **自動** | flags と診断結果からの候補抽出のみ |
| カテゴリ分類の最終承認 | **人間** | ロジック変更の要否を含む判断 |
| version 更新の決定 | **人間** | ロジック変更の実行判断 |

---

## 1. スコープ

### 1.1 扱うもの

1. **月次フィードバック** — 既存 artifact を入力とした 6 軸の自動診断とドラフト生成
2. **月次監査** — review flags の自動評価と異常検出
3. **ロジック精査** — 月次で見直してよい項目と固定すべき項目の分離
4. **version 更新判断** — 更新タイミング・更新単位・変更記録・禁止事項

### 1.2 扱わないもの

- 日次 forecast 生成ロジック
- 日次 outcome / evaluation 生成ロジック
- 自動売買
- 自動キャリブレーション
- 任意日付での ad-hoc な仕様変更

---

## 2. 月次レビューの入力

月次レビューは、以下の既存 artifact を入力とする。新しい観測や新しい artifact は追加しない。

artifact の収集と存在検証は自動で行う。欠損 artifact がある場合は、診断を中断せず、欠損を記録した上で利用可能な artifact のみで診断を進める。

| # | artifact | 供給元 | 自動検証 |
|---|----------|--------|----------|
| 1 | `monthly_review.json` | fx_monthly_review_v1 | 存在・スキーマ検証 |
| 2 | `monthly_review.md` | fx_monthly_review_v1 | 存在検証 |
| 3 | `monthly_strategy_metrics.csv` | fx_monthly_review_v1 | 存在・列検証 |
| 4 | `monthly_slice_metrics.csv` | fx_monthly_review_v1 | 存在・列検証 |
| 5 | `monthly_review_flags.csv` | fx_monthly_review_v1 | 存在・列検証 |
| 6 | `input_snapshot.json` | fx_observability_artifacts_v1 | 存在・スキーマ検証 |
| 7 | `run_summary.json` | fx_observability_artifacts_v1 | 存在・スキーマ検証 |
| 8 | `daily_report.md` | fx_observability_artifacts_v1 | 存在検証 |
| 9 | `scoreboard.csv` | fx_observability_artifacts_v1 | 存在・列検証 |
| 10 | `provider_health.csv` | fx_observability_artifacts_v1 | 存在・列検証 |

---

## 3. 月次レビューの 6 軸観点

月次レビューでは、以下の 6 軸をこの順序で **自動診断** する。各軸の診断結果は構造化データとして出力され、decision log ドラフトに統合される。

### 3.1 基本成績

UGH と各 baseline の以下 metrics を自動集計する。

- forecast_count
- direction_hit_rate
- range_hit_rate
- mean_abs_close_error_bp
- mean_abs_magnitude_error_bp

### 3.2 baseline 差分

各 baseline に対する以下の差分を自動算出する。判定は差分表で行い、感想ではない。

- direction accuracy delta
- mean absolute close error delta
- mean absolute magnitude error delta

baseline に対する明確な劣後が検出された場合、自動的に flag を付与する。

### 3.3 state / regime / event slice

UGH のみを対象とし、以下の slice 別成績を自動集計する。

- dominant_state
- regime_label
- volatility_label
- intervention_risk
- event_tag

特定 slice への成績偏りが検出された場合、自動的に flag を付与する。

### 3.4 disconfirmer / false positive

以下を自動集計する。

- direction_hit == false の代表例の抽出
- disconfirmer_explained == true の割合の算出
- dominant_state / event_tag / regime_label ごとの false positive 集中の検出

### 3.5 provider / observability

以下を自動集計する。

- missing windows
- provider lag rate
- fallback adjustment rate
- provider mix
- annotation coverage rate

provider / missing window 問題または annotation coverage 問題が検出された場合、自動的に flag を付与する。

### 3.6 review flags

既存 `fx_monthly_review_v1` が出す review flags を自動で読み込み、分類する。

flags は change candidate list ドラフトの材料として自動で反映されるが、最終的なカテゴリ分類の承認は人間が行う。

---

## 4. 月次判断カテゴリ

自動診断の結果、各項目を以下の 4 カテゴリのいずれかにドラフト分類する。**最終承認は人間が行う。**

| カテゴリ | 意味 | version 更新 |
|---|---|---|
| **Keep** | 現行ロジック維持 | しない |
| **Logic audit** | ロジック精査対象として次月検討へ送る | しない |
| **Data / provider remediation** | データ基盤・運用基盤の問題として扱う | しない |
| **Version promotion candidate** | 翌月の version 更新候補として採択 | する（theory / engine / schema / protocol のいずれか） |

### 4.1 自動ドラフト分類の条件例

- **Keep** — baseline 差分が許容範囲内であり、slice 別成績にも偏りが見られず、provider / annotation の flag もない場合
- **Logic audit** — baseline に対して特定 slice で劣後が見られるが、provider / annotation に起因する flag がない場合。次月に精査を行い、version 更新の要否を判断する
- **Data / provider remediation** — missing windows の集中、provider lag rate の悪化、annotation coverage rate の低下など、入力データ品質に起因する flag が検出された場合
- **Version promotion candidate** — 過去の Logic audit で原因が特定済みであり、変更内容が明確で、変更前後の影響範囲が限定できる場合

### 4.2 人間承認の要件

自動ドラフト分類はあくまで提案である。人間は以下を行う。

- ドラフト分類の妥当性を確認する
- 必要に応じてカテゴリを変更する
- Version promotion candidate については、変更内容・影響範囲を精査した上で承認する

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

月次レビューの成果物として、以下の 3 つを生成する。ドラフトは自動生成され、人間承認を経て確定する。

### 7.1 Monthly decision log

自動生成されるドラフトに対し、人間が overall_judgment と final_recommendation を承認して確定する。

| フィールド | 説明 | 生成 |
|-----------|------|------|
| review_month | レビュー対象月 | 自動 |
| overall_judgment | Keep / Logic audit / Data/provider remediation / Version promotion candidate | 自動ドラフト → 人間承認 |
| key_flags | review flags のうち判断に影響したもの | 自動 |
| baseline_comparison_summary | baseline 差分の要約 | 自動 |
| logic_audit_candidates | ロジック精査候補のリスト | 自動 |
| provider_annotation_concerns | provider / annotation に関する懸念事項 | 自動 |
| final_recommendation | 最終的な recommendation | 自動ドラフト → 人間承認 |

### 7.2 Change candidate list

自動生成される候補リストに対し、人間が category と status を承認して確定する。

| フィールド | 説明 | 生成 |
|-----------|------|------|
| candidate_id | 候補の識別子 | 自動 |
| category | Keep / Logic audit / Data/provider remediation / Version promotion candidate | 自動ドラフト → 人間承認 |
| rationale | 候補とした理由 | 自動 |
| expected_benefit | 期待される改善 | 自動 |
| expected_risk | 想定されるリスク | 自動 |
| owner | 担当 | 人間が指定 |
| status | 候補のステータス | 人間が指定 |

### 7.3 Version decision record

version 更新の判断は人間が行う。自動生成される診断結果を材料として、人間が全フィールドを確定する。

| フィールド | 説明 | 生成 |
|-----------|------|------|
| update_performed | version 更新を実施したか (bool) | 人間 |
| updated_versions | 更新した version の一覧 | 人間 |
| unchanged_versions | 更新しなかった version の一覧 | 人間 |
| freeze_window_start | 凍結期間の開始日 | 人間 |
| freeze_window_end | 凍結期間の終了日 | 人間 |
| rollback_trigger | rollback を発動する条件 | 人間 |

---

## 8. 判断手順

毎月の実行順序を以下の 10 ステップで固定する。順序を崩してはならない。

| ステップ | 内容 | 実行 |
|---------|------|------|
| 1 | 月次 artifact を確定する | 自動（収集・存在検証） |
| 2 | strategy metrics を見る | 自動（集計・比較） |
| 3 | baseline comparison を見る | 自動（差分算出・flag 付与） |
| 4 | state / regime / event slice を見る | 自動（slice 集計・偏り検出） |
| 5 | false positive / disconfirmer を見る | 自動（集計・集中検出） |
| 6 | provider / annotation 健全性を確認する | 自動（集計・異常検出） |
| 7 | review flags を確認する | 自動（読み込み・分類） |
| 8 | keep / audit / remediation / promotion candidate に分類する | 自動ドラフト → 人間承認 |
| 9 | version 更新可否を決定する | 人間 |
| 10 | monthly decision log を確定する | 人間承認 |

順序固定の理由: 理論問題と運用問題を混同しないため。

自動実行（ステップ 1〜7）は月次 artifact が揃った時点で自動的に開始される。人間介入（ステップ 8〜10）は自動診断の完了後に行う。

---

## 9. 判定の優先順位

判断が衝突した場合の優先順位を以下の順で定める。この優先順位は自動ドラフト分類にも適用される。

1. provider / missing window 問題
2. annotation coverage 問題
3. baseline に対する明確な劣後
4. state / disconfirmer の偏り
5. magnitude / range の改善余地

設計意図: 入力が壊れているのに理論をいじらない。

---

## 10. 完了条件

月次レビューは、以下をすべて満たしたときに完了とする。

- 自動診断（ステップ 1〜7）がすべて実行され、結果が記録された
- 6 軸観点（セクション 3）のすべてについて診断結果が出力された
- 自動ドラフト（decision log / change candidate list）が生成された
- 人間がカテゴリ分類を承認した（ステップ 8）
- 人間が version 更新可否を決定した（ステップ 9）
- Monthly decision log / Change candidate list / Version decision record の 3 成果物が確定した（ステップ 10）
- version 更新を行う場合、version 更新ルール（セクション 6）に従った記録を残した

---

## 11. 一文定義

> FX Monthly Review / Feedback / Logic Audit Protocol v1 とは、月次集計済み artifact 群を用いて、UGH と baseline の比較、state / event / provider / annotation の診断、変更候補の分類、version 更新可否の判断を、監査可能な順序と記録形式で行う governance protocol である。フィードバックと監査は自動実行され、ロジック変更を伴う判断のみ人間が承認する。
