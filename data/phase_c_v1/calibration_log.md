# Phase C v0 → v1 校正ログ

## 実行環境
- scorer_backend: sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)
- tokenizer: regex_fallback
- model: gpt-4o
- run_date: 2026-03-21

### アーティファクト整合性に関する注記
本ログおよび `phase_c_v1_results.csv`・PNG可視化はSTバックエンドでの再採点結果に基づく。
以下の旧アーティファクトはtfidf-char-ngramバックエンド時点のまま未更新：
- `phase_c_scored_v1.jsonl` (`"backend": "tfidf-char-ngram"`)
- `phase_c_report_v1.html` (`scorer_backend=tfidf-char-ngram`)

これらはv0→v1のコード変更検証用として保持しており、数値比較にはCSVを正とする。

## 変更点
1. por_fired: > → >= に修正（Step 1）
2. grv: ストップワード除去、カタカナ結合、品詞フィルタ追加（Step 2）
3. ΔE: 3パターン計算を追加 — core/full/summary（Step 3）
4. delta_e のプライマリ値を delta_e_full に切り替え

## v0 → v1 比較

### PoR
- v0 平均: 0.800
- v1 平均: 0.7992
- v0 発火数: 49/102 (temp=0.0, 48.0%)
- v1 発火数（temp=0.0）: 49/102 (48.0%) — v0と同一母数で比較
- v1 発火数（全温度）: 149/306 (48.7%)
- 備考: temp=0.0同士で比較すると発火率は同一(48.0%)。>=閾値修正の影響は
  境界値(por==0.82)のサンプルが存在しないため、この102件では差が出ていない。

### ΔE
- v0 平均（core のみ）: 0.516
- v1 delta_e_core 平均: 0.5091
- v1 delta_e_full 平均: 0.3006
- v1 delta_e_summary 平均: 0.5051
- v1 ΔE full 四分位: Q1=0.2026 / median=0.2832 / Q3=0.3761
- v1 ΔE full 帯域分布:

| ΔE full 範囲 | ラベル | 件数 | 比率 |
|---|---|---|---|
| ≤ 0.20 | 意味的に近い | 75 | 25% |
| 0.20〜0.38 | 中間的ズレ | 156 | 51% |
| > 0.38 | 意味乖離 | 75 | 25% |

### grv
- v0 不正トークン例: があります, します, クナイゼ, プンソ
- v1 不正トークン数: 4種6行残存（grv_top内。助詞接続の断片・複合語の断片が未除去）
  - "いことは" 1件, "注意重" 2件, "をつく" 2件, "づいて" 1件
- v1 grv_top 出現頻度上位5語:
  1. モデル: 39件
  2. データ: 12件
  3. 意識: 8件
  4. 理解: 7件
  5. 意味: 6件

## 所見
- ΔE full（reference全文比較）で弁別力が回復。v0の全件「意味乖離」から、0.07〜0.72の実用的分布に改善。
- カテゴリ別ΔE full: epistemology(0.2049) < ai_philosophy(0.2325) < adversarial(0.2761) < ai_ethics(0.3052) < technical_ai(0.3270) < ugh_theory(0.4216)
- カテゴリ別PoR: ai_ethics(0.8404) > ai_philosophy(0.8200) > adversarial(0.8120) > epistemology(0.8102) > technical_ai(0.7702) > ugh_theory(0.7520)
- 両指標は大筋で整合するが、上位カテゴリでは順位が入れ替わる。
  PoRではai_ethicsが最上位だがΔE fullではepistemologyが最良。
  下位（technical_ai, ugh_theory）は両指標で一致。
  ai_ethicsは「共鳴はしているが核心からはズレている」パターンを示す。
- GPTはUGHer固有概念に対して最もreferenceから遠く、認識論的問いに最も近い。
- PoR平均はv0とほぼ同値(0.800→0.7992)。temp=0.0同士では発火率も同一(48.0%)。
- grv不正トークンはv0の4種から4種6行に微減。fugashi導入時に完全除去が期待される。
- **既知の不整合**: q005 (temp=1.0) で por=0.82 / por_fired=False となっている。
  CSV export 時の浮動小数点丸めと判定フラグの不整合が原因と推定。
  実害は1件のみ。rescore スクリプトに整合チェック追加を推奨（TODO）。
- **分析母集団**: 閾値設定・統計分析は role=test の 288件（96問×3温度）を
  母集団とすべき。baseline(4問×3温度=12件) と grv_calibration(2問×3温度=6件) は校正用であり、
  本分析の母集団に含めない。
  role=test の ΔE full 平均は 0.2952（全体 0.3006 と微差）。
