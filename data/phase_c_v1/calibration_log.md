# Phase C v0 → v1 校正ログ

## 実行環境
- scorer_backend: tfidf-char-ngram（sentence-transformers モデルDL不可のため文字n-gram TF-IDF代替）
- tokenizer: regex_fallback（fugashi辞書ビルド不可のため正規表現フォールバック）
- model: gpt-4o（v0で使用したモデル）
- run_date: 2026-03-21

## 変更点
1. por_fired: > → >= に修正（Step 1）
2. grv: ストップワード除去、カタカナ結合、品詞フィルタ追加（Step 2）
3. ΔE: 3パターン計算を追加 — core/full/summary（Step 3）
4. delta_e のプライマリ値を delta_e_full に切り替え

## v0 → v1 比較

### PoR
- v0 平均: 0.800
- v1 平均: 0.3964
- v0 発火数: 49/102
- v1 発火数: 0/306
- 備考: v1はtfidf-char-ngram backendのため、embedding-baseのv0と直接比較不可。
  v0はsentence-transformersによる意味的類似度、v1は文字n-gramの表層一致度を計測しており
  スケールが異なる。sentence-transformers backend での再採点を推奨。

### ΔE
- v0 平均（core）: 0.516
- v1 delta_e_core 平均: 0.8647
- v1 delta_e_full 平均: 0.7020
- v1 delta_e_summary 平均: 0.8891
- v0 全件「意味乖離」: Yes (100%)
- v1 で閾値0.10以下の件数: 0/306
- 備考: tfidf-char-ngram backendではΔEが高めに出る傾向あり（コサイン類似度の粒度が粗い）。
  3パターン分離（core/full/summary）の実装は正常動作を確認済み。

### grv
- v0 不正トークン例: があります, します, クナイゼ, プンソ
- v1 不正トークン: 5種13件残存（grv_top10内。助詞接続の断片が未除去）
  - "づいて" 5件, "をつく" 3件, "のような" 3件, "さには" 1件, "くても" 1件
- 備考: fugashi未使用のため品詞フィルタは未適用。正規表現+拡張ストップワードで
  大半の不正トークンは除去済みだが、活用語尾・助詞接続の断片が一部残存する。
  fugashi導入時に再フィルタを推奨。

## 所見
v1のコード変更（por_fired>=修正、grv改善、ΔE三値分離）は正常に動作している。
ただしbackendがtfidf-char-ngramのため、v0（sentence-transformers）との数値直接比較は意味を持たない。
sentence-transformers環境での再採点を行い、同一backend間で校正値を確定させる必要がある。
