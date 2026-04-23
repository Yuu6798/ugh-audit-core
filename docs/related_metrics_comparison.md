# 関連評価指標との比較 — PoR/ΔE の位置付け

NLP2027 投稿論文 (Variant 1: フレームワーク提案重視) の Related Work 節の
裏付け資料。本リポジトリで提案する PoR/ΔE フレームワークが既存の LLM/NLG
評価指標群に対して何を新たに提供するかを、軸ごとに対照する。

---

## 1. 既存指標の 3 系統分類

LLM/NLG 出力評価の主要指標は、計算機構の観点で 3 系統に整理できる。

| 系統 | 代表指標 | 計算機構 |
|---|---|---|
| **(a) 表層類似度系** | BLEU, ROUGE | n-gram overlap、reference 必須 |
| **(b) 埋め込み類似度系** | BERTScore, BLEURT | 事前学習言語モデルの意味埋め込み距離 |
| **(c) LLM 駆動評価系** | G-Eval, Prometheus, RAGAS, LLM-as-Judge | LLM 自体を judge として使用、prompt / rubric 駆動 |

各系統には共通の構造的限界があり、本研究の差別化点はそこに直接対応する (§4)。

---

## 2. 評価軸別の対照表

提案手法 PoR/ΔE と既存 8 指標を、本論文の貢献に直結する 5 軸で比較する。

| 指標 | 構造/内容の分離 (S 軸/C 軸) | sub-score 分解 (解釈可能性) | 参照不要 | LLM 非依存 | 計算コスト |
|---|---|---|---|---|---|
| BLEU | × | × (単一スコア) | × | ◎ | 低 |
| ROUGE | × | × (単一スコア) | × | ◎ | 低 |
| BERTScore | × | △ (P/R/F1 のみ) | × | ◎ (PLM のみ) | 中 |
| BLEURT | × | × (回帰スカラ) | × | ◎ (PLM のみ) | 中 |
| G-Eval | △ (prompt 設計依存) | △ (criterion 依存) | ◎ | × (GPT-4 依存) | 高 |
| Prometheus / Prometheus 2 | × | △ (NL feedback) | △ | △ (open LM) | 高 |
| RAGAS | × | ○ (RAG 4 軸限定) | ◎ | × | 高 |
| LLM-as-Judge / MT-Bench | × | △ (category 単位) | ◎ | × (judge LM 依存) | 高 |
| **PoR/$\Delta$E (提案)** | **◎ (architectural)** | **◎ ($L_{\mathrm{sem}}$ 3 項分解)** | △ (リファレンス補強あり) | ◎ (cascade + SBert) | 中 |

凡例: ◎ 完全対応 / ○ 部分対応 / △ 条件付き対応 / × 非対応

### 主要な観察

1. **構造/内容の分離 (S 軸 / C 軸)**: 既存指標で architectural に分離している
   ものは存在しない。G-Eval は prompt 設計次第で疑似的に分離可能だが、軸の
   排他性も網羅性も保証されない。
2. **sub-score 分解**: BERTScore の P/R/F1、RAGAS の 4 軸 (RAG 限定)、
   Prometheus の自然言語フィードバックなど部分的な分解はあるが、再利用可能な
   診断 primitive (operator loss / unknown loss / causal-structure loss)
   への分解は本研究が初。
3. **LLM 非依存**: G-Eval / Prometheus / RAGAS / MT-Bench は judge LLM の
   バージョン更新で評価が変動する再現性問題がある。提案手法は cascade matcher
   と SBert に基づき、judge LLM を使わない。

---

## 3. 各指標の詳細

### 3.1 表層類似度系

#### BLEU (Papineni et al. 2002)

- **機構**: 候補と参照の n-gram precision の幾何平均 + brevity penalty。
- **限界**:
  - 構文的整合性と意味的妥当性が単一スコアに混在
  - paraphrase に対して脆弱
  - segment-level の人手評価との相関が低い

#### ROUGE (Lin 2004)

- **機構**: 参照に対する n-gram recall (ROUGE-N) または LCS (ROUGE-L)。
- **限界**:
  - 単一の overlap ratio に集約され失敗モードを区別できない
  - tokenization に敏感
  - abstractive summarization での信頼性低下

### 3.2 埋め込み類似度系

#### BERTScore (Zhang et al. 2020)

- **機構**: 候補と参照の token 単位 contextual embedding の cosine 類似度を
  greedy matching で集約し precision/recall/F1 を出力。
- **限界**:
  - paraphrase 耐性は向上したが、構造完全性と内容被覆は依然分離不能
  - 流暢だが内容が薄い出力と、構造が崩れているが内容を含む出力を区別しない
  - score の絶対値が解釈困難 (calibration を要する)

#### BLEURT (Sellam et al. 2020)

- **機構**: BERT を合成データ + 人手 rating で fine-tune した回帰モデル。
  候補-参照ペアから人手品質スコアを直接予測。
- **限界**:
  - 出力は単一スカラで完全にブラックボックス
  - 失敗モードに対応する診断 sub-score を返せない
  - ドメイン適応のため再 fine-tune が必要

### 3.3 LLM 駆動評価系

#### G-Eval (Liu et al. 2023, EMNLP 2023)

- **機構**: GPT-4 に評価基準と Chain-of-Thought を与え、確率重み付きの
  form-filling で多次元スコアを生成。
- **限界**:
  - 軸の分離は prompt 設計に完全依存し architectural な保証なし
  - GPT-4 のバージョン更新で評価が変動 (再現性の弱点)
  - 軸の相互排他性・網羅性が形式的に保証されない

#### Prometheus / Prometheus 2 (Kim et al. 2024)

- **機構**: GPT-4 が生成した fine-grained 評価データで open-source LM
  (13B〜) を fine-tune。rubric 条件付きスコア + 自然言語フィードバックを
  返す。Prometheus 2 は pairwise ranking にも対応。
- **限界**:
  - 軸定義はユーザー rubric に委譲、内蔵の構造/内容分離なし
  - 自然言語フィードバックは定性的説明だが、定量的 sub-score 分解ではない
  - 学習データが GPT-4 由来のため judge bias を継承

#### RAGAS (Es et al. 2024, EACL 2024)

- **機構**: faithfulness / answer relevancy / context precision /
  context recall の 4 指標で RAG パイプライン全体を分解評価。
- **限界**:
  - 4 軸分解は明確だが RAG 文脈に特化、一般生成評価には適用不可
  - 構造的整合性 vs 命題カバレッジの分離ではなく、retrieval 品質 vs
    generation 品質の分離
  - LLM API への依存により再現性に難あり

#### LLM-as-Judge / MT-Bench (Zheng et al. 2023, NeurIPS 2023)

- **機構**: GPT-4 を judge として、80 問の multi-turn 指示追従課題に対して
  1-10 の holistic score または pairwise comparison を実施。
- **限界**:
  - score は単一の opaque 判定で sub-score 分解なし (category 単位の
    粗い診断のみ)
  - position bias / verbosity bias / self-enhancement bias が報告されている
  - judge LM のバージョン更新で score が変化

### 3.4 関連サーベイ (2024-2025)

- Chang et al. (2024). "A Survey on Evaluation of Large Language Models."
  *ACM TIST* 15(3), Article 39. arXiv:2307.03109
- Li et al. (2024). "LLMs-as-Judges: A Comprehensive Survey on LLM-based
  Evaluation Methods." arXiv:2412.05579
- Tan et al. (2024). "A Survey on LLM-as-a-Judge." arXiv:2411.15594

---

## 4. PoR/$\Delta$E の差別化点

本研究の貢献を、上記既存指標との対比で 4 点に整理する。

### (i) 構造完全性 (S 軸) と命題カバレッジ (C 軸) の architectural な分離

既存指標はいずれも S 軸と C 軸を**単一スコアに混合**している
(BLEU/ROUGE/BERTScore/BLEURT) か、**prompt 設計に分離を委譲**している
(G-Eval/Prometheus)。本研究では 2 軸を計算機構の段階で分離し、応答の
位置付けを 2 次元座標 PoR で明示する。

### (ii) 意味損失 $L_{\mathrm{sem}}$ の 3 項分解

判定根拠の解釈可能性は、既存指標では BERTScore の P/R/F1、RAGAS の RAG 4 軸、
Prometheus の自然言語フィードバックといった部分的・限定的な提供に留まる。
本研究は $L_{\mathrm{sem}}$ を演算子 (operator) / 未知性 (unknown) /
因果構造 (causal-structure) の 3 項に分解し、判定が **どの損失項に駆動された
か** を再利用可能な diagnostic primitive として提供する。

### (iii) judge LLM 非依存による再現性

LLM 駆動評価系 (G-Eval, Prometheus, RAGAS, MT-Bench) は judge モデルの
バージョン更新で評価が変動する根本的な再現性問題を抱える。本研究は
cascade matcher (Tier 1: surface / Tier 2: SBert / Tier 3: 多条件) と
SBert に基づき、judge LLM を計算経路に持たない。

### (iv) open-source / 完全再現可能

RAGAS, MT-Bench は商用 LLM API への依存が前提だが、本研究は MIT ライセンス
で公開された single repository で完結し、CI による数値整合性 guard
(`tests/test_validation_ci.py`) により論文中の主要数値と実装の同期を
強制する。

---

## 5. NLP2027 論文での扱い

`paper/sections/related_work.tex` では紙幅 (約 800 字) の制約から、以下の
構成で集約する:

- **2.1 表層・埋め込み類似度指標**: BLEU/ROUGE/BERTScore/BLEURT を
  「単一スコアの限界」として纏める
- **2.2 LLM 駆動評価**: G-Eval/Prometheus/RAGAS/LLM-as-Judge を
  「軸分離が prompt 設計依存である問題」として纏める
- **2.3 解釈可能な評価指標**: 軽く触れる (本論文の解釈可能性主張への伏線)
- **2.4 本研究の位置付け**: §4 の (i)(ii) を中心に明示

詳細サーベイ・全比較表・各指標の限界の網羅は、本ドキュメントが論文の
external reference として機能する (Data Availability で github URL 経由で
査読中も参照可能、最終 camera-ready で正式リンク化)。

---

## 6. BibTeX エントリ

`paper/references.bib` に転記済み。エントリ key は以下:

| 指標 | BibTeX key |
|---|---|
| BLEU | `papineni2002bleu` |
| ROUGE | `lin2004rouge` |
| BERTScore | `zhang2020bertscore` |
| BLEURT | `sellam2020bleurt` |
| G-Eval | `liu2023geval` |
| Prometheus | `kim2024prometheus` |
| Prometheus 2 | `kim2024prometheus2` |
| RAGAS | `es2024ragas` |
| LLM-as-Judge / MT-Bench | `zheng2023judging` |
| Survey on Eval of LLMs (Chang 2024) | `chang2024survey` |
