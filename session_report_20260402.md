# セッションレポート 2026-04-02
## cascade 本実装マージ + ベースライン確定

## 全体ステータス

| 指標 | セッション前 | セッション後 |
|------|------------|------------|
| 命題ヒット率 | 59.4% (184/310) | 61.0% (189/310) |
| tfidf hit | 184 | 184（回帰ゼロ） |
| cascade_rescued | — | 5 |
| miss | 126 | 121 |
| 方向性一致率 (HA20) | 19/20 (0.95) | 19/20 (0.95) |
| Spearman ρ (system vs human) | 0.4030 | 0.4030 |
| テスト | 256 collected, 251p/6s | 256 collected, 256p/3s |

## 実施内容

### Step 1: 現状確認
- cascade_matcher.py パラメータ: 全一致 ✓
- pytest: 251 passed, 6 skipped ✓
- synonym_dict: 110 キー ✓
- ベースライン CSV: 181/310（Layer 6 synonym 追加前の値。現 tfidf ベースラインは 184 が正）
- 判定: **PASS**（CSV の 181 vs 手順書の 184 の差分は Layer 6 synonym によるもの。原因特定済み）

### Step 2: cascade パイプライン統合
- detector.py: +68行（cascade import + フォールバック + detect() 内統合）
- ugh_calculator.py: +2行（hit_sources フィールド追加）
- cascade_matcher.py: 変更なし
- フォールバック: cascade_matcher 未 import 時に Tier 1 のみで正常動作確認
- sentence-transformers インストール → skip 6→3 に解消
- pytest: 256 passed, 3 skipped, 0 failed
- 判定: **PASS**（受け入れ条件 6/6 全 PASS）

### Step 3: 全件リラン・新ベースライン
- 新ベースライン: 189/310 (61.0%)
- tfidf: 184（回帰ゼロ確認）
- cascade_rescued: 5件（PoC 5件と完全一致）
- dev_cascade_20 以外の新規 rescue: なし（atomic_units が15問分のみのため）
- 判定: **PASS**（受け入れ条件 4/4 全 PASS）

### Step 4: ρ ゲート判定
- 方向性一致率: 19/20 (0.95) — 変化なし
- Spearman ρ (system vs human): 0.4030 (p=0.078)
- cascade 効果: tfidf only 0.3824 → cascade あり 0.4030 (+0.0206)
- HA20 内 cascade 影響: q080 のみ（hit_rate 0.667→1.000、人間アノテーションと完全一致に改善）
- ρ 定義の整理: 旧「0.9090」は人間アノテーター内部一貫性であり system 指標ではない
- 判定: **PASS** (0.95 ≥ 0.90)

### Step 5: CLAUDE.md 更新
- +39行, -4行
- Cascade Matcher セクション: 統合済みステータスに更新
- Baseline & HA20 セクション: 新設（189/310, 3指標併記）
- synonym_dict キー数: 122→110 に修正（実測値）
- テスト数: 256 collected, 3 skipped 明記

## hit_source 内訳

| hit_source | 件数 | 割合 |
|-----------|------|------|
| tfidf | 184 | 59.4% |
| cascade_rescued | 5 | 1.6% |
| miss | 121 | 39.0% |
| 合計 | 310 | 100% |

## カテゴリ別変化

| カテゴリ | 旧 hits/total (%) | 新 hits/total (%) | Delta |
|---------|-------------------|-------------------|-------|
| adversarial | 21/42 (50.0%) | 21/42 (50.0%) | +0 |
| ai_ethics | 34/58 (58.6%) | 35/58 (60.3%) | +1 |
| ai_philosophy | 38/55 (69.1%) | 39/55 (70.9%) | +1 |
| epistemology | 28/42 (66.7%) | 29/42 (69.0%) | +1 |
| technical_ai | 32/58 (55.2%) | 37/58 (63.8%) | +5 |
| ugh_theory | 28/55 (50.9%) | 28/55 (50.9%) | +0 |

注: technical_ai の +5 は Layer 6 synonym +3 と cascade rescue +2 の合算。

## cascade_rescued 命題一覧

| 命題 | カテゴリ | selection_category | 命題テキスト |
|------|---------|-------------------|------------|
| q016_p0 | ai_ethics | concept_absent | 「誰の意図か」の決定が権力行使 |
| q046_p1 | technical_ai | ovl_insufficient | 学習時に未獲得の知識は生成されない |
| q048_p2 | technical_ai | ovl_insufficient | ブラックボックスの部分的照明にとどまる |
| q064_p0 | ai_philosophy | concept_absent | 嘘は意図的欺瞞を前提とする |
| q080_p2 | epistemology | ovl_insufficient | 準証言的機能としての概念拡張が検討可能 |

詳細値は `data/eval/audit_102_main_baseline_cascade.csv` を参照。

## 判明した課題・知見

### 1. ρ 定義の混乱
- 旧「ρ=0.9090」は人間アノテーター内部一貫性（reference ρ）であり、system 評価指標ではなかった
- system vs human の Spearman ρ は 0.40 前後。n=20 では統計的有意性が弱い (p=0.078)
- 今後は 方向性一致率 / system ρ / reference ρ の3指標を区別して管理する

### 2. cascade rescue の天井
- atomic_units が定義されている 15 問（dev_cascade_20 由来）以外では c5 条件を満たせず rescue 不可
- n=48 拡張時に atomic_units を全問に展開すれば、追加の rescue が見込まれる

### 3. ベースライン CSV の世代管理
- round4 CSV (181) と現 tfidf (184) の乖離は Layer 6 synonym 追加後に CSV を再生成していなかったため
- 今回 cascade CSV を新世代ベースラインとして生成。今後は CSV 生成タイミングを明確にする

## 次セッションへの引き継ぎ

### 最優先
- **n=48 アノテーション拡張**: ρ の統計的検出力を確保するための最重要タスク
  - atomic_units の全問展開も同時に行う → cascade rescue 範囲が拡大
  - n=48 での ρ 安定性が cascade 方針の最終判断基準

### 準備可能
- **Gemini 再取得**: Phase 3 前に並行して開始可能
- **fr 閾値緩和 (Phase 2, Task D)**: cascade 効果と独立に測定すること。今回のセッションでは未着手

### 中期課題
- ρ 定義の正式整理（3指標の使い分けをドキュメント化）
- adversarial / ugh_theory カテゴリの改善（cascade 効果なし → 別アプローチ要検討）
