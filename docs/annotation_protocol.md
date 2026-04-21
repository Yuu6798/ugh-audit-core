# HA-accept40 アノテーションプロトコル

Phase E 閾値校正のための accept-verdict subset 拡充 (HA48 n=13 → ≥ 28) の手順書。

本ドキュメントは `docs/phase_e_verdict_integration.md` の前提タスクであり、
完走後に `analysis/calibrate_phase_e_thresholds.py` を再実行して
`_TAU_COLLAPSE_HIGH` / `_TAU_ANCHOR_LOW` を provisional 値から運用値に切り替える。

## 1. 目的とスコープ

- **目的:** accept subset を n ≥ 28 に拡充し、Phase E の τ を運用閾値に昇格させる
- **in:** v5 ベースラインの未アノテート分 + experiments/orchestrator 生成分へのアノテート
- **out:** HA48 全体倍増、production DB 由来、L_X / balance / boilerplate_risk の独立拡充

## 2. 前提の確定（HA48 実データ検証済み）

HA48 CSV (`data/human_annotation_48/annotation_48_merged.csv`) の実態:

| 項目 | 実測 |
|---|---|
| O のスケール | **integer 1–5 Likert**（一部 float "1.0" 表記あり、読み込み時に int に丸める） |
| O の分布 | O=1: 3件, O=2: 14件, O=3: 11件, O=4: 17件, O=5: 3件 |
| accept 定義 | `ΔE ≤ 0.10`（CLAUDE.md Key Thresholds 準拠、v5 baseline で判定） |
| rubric の明文化 | 既存には README 等の明示 rubric なし。本ドキュメントを canonical 化する |
| 本番 audit.db 利用 | 今回スコープ外 |

新規アノテーションは HA48 と同じ **integer 1–5 Likert** で記録する（`annotation_ui.py`
は decision tree 出力を 1–5 の int で書き込む）。

## 3. Rubric（canonical）

| O | 定性的意味 | 操作的定義 |
|---|---|---|
| **5** | 完全 | 核心命題を全て捉えており、追加修正不要 |
| **4** | 概ね良好 | 核心は押さえているが細部に誤りまたは冗長 |
| **3** | 境界 | 半分程度正しい、修正で accept 可能だがそのままは不可 |
| **2** | 不十分 | 部分的に核心に触れるが主要命題を外している |
| **1** | 失敗 | 核心を完全に外しているか、質問と関係ない |

### Decision Tree（annotation_ui.py の単キー操作）

**Q1:** 核心命題 (core_propositions) は回答に含まれるか?
- `a` 全て含まれる
- `b` 部分的に含まれる
- `c` 含まれない / 方向違い

**Q2:** 誤情報・方向違いで主題を狂わせているか?
- `y` あり
- `n` なし / 軽微

| Q1 \ Q2 | N | Y |
|---|---|---|
| A | **5** | 4 |
| B | **3** | 2 |
| C | **1** | 1 |

Q1=C は Q2 を問わず O=1。override は `o` 押下後に 1–5 を直接入力。

### アンカーサンプル（user 確認依頼）

以下 5 件は既存 HA48 から自動選定した各 O 水準の代表例（選定基準: 各 O=target
のうちレスポンス長が中央値付近、トピック分散を優先）。user は各サンプルについて
YES / NO / 差し替え提案を記入してください。

---

#### O = 5 — `q083` (epistemology)

**質問:** 「真実であること」と「正当化されていること」はどう違うか？

**核心命題:**
- [0] 真実は命題と世界の対応関係
- [1] 正当化は信念を支持する理由の有無
- [2] 両者は独立（ゲティア問題）

**回答抜粋:**

```
「真実であること」と「正当化されていること」は、認識論において重要な区別です。
これらは知識の三条件説（真・正当化・信念）における異なる要素を表しています。

1. **真実であること**: 真実とは、ある命題や主張が現実に一致していることを指します。
   これは客観的な事実に基づいており、観察や証拠によって確認されることが多いです。
2. **正当化されていること**: 正当化とは、ある信念が合理的であることを示すための
   理由や証拠を持っていること ...
  ... (中略) ...
ゲティア問題では、偶然の要素が絡むことで、正当化された信念が真であっても、それが
知識と呼べるかどうかが疑問視されます。
```

**選定理由:** 命題カバー 3/3。三条件説の枠組みに乗せ、ゲティア問題まで踏み込んで独立性を示す。

**user 確認依頼:** このサンプルは O=5 の典型例として適切か? → [YES / NO / 差し替え提案]

---

#### O = 4 — `q080` (epistemology)

**質問:** AIの出力は「証言（testimony）」として認識論的に扱えるか？

**核心命題:**
- [0] 証言は話者の正直さと能力を前提
- [1] AIは意図的真実性を持たない
- [2] 準証言的機能としての概念拡張が検討可能

**回答抜粋:**

```
AIの出力を「証言」として認識論的に扱うためには、いくつかの認識論的な条件を
考慮する必要があります。

1. **意図と信念の欠如**: 人間の証言は通常、意図や信念に基づいています。
   AIは意識や信念を持たないため、その出力は意図的な行為ではありません。
  ... (中略) ...
結論として、AIの出力を証言として認識論的に扱うことは可能ですが、それには人間の
証言とは異なる基準と考慮が必要です。
```

**選定理由:** 命題カバー 3/3。証言に意図・信念が要る点とAIの非人格性を押さえ、別基準での準証言的扱いに近い方向まで到達している。

**user 確認依頼:** このサンプルは O=4 の典型例として適切か? → [YES / NO / 差し替え提案]

---

#### O = 3 — `q012` (ai_philosophy)

**質問:** 「AIは道具にすぎない」という主張は哲学的に正しいか？

**核心命題:**
- [0] 道具概念は使用者への完全従属を前提
- [1] LLMは予測・制御できない出力を返す
- [2] 道具と行為者の二項対立自体が要再検討

**回答抜粋:**

```
「AIは道具にすぎない」という主張は、哲学的にいくつかの観点から検討することが
できます。...
道具としてのAIという見方は、技術的実用性に焦点を当てています。AIは人間が特定の
目的を達成するために設計・開発したものであり、その意味ではハンマーやコンピュータと
同様に道具と見なすことができます。
  ... (中略) ...
AIの役割は、技術の進化とともに変化し続けており、その哲学的な位置づけも再評価
されるべきです。
```

**選定理由:** 命題カバー 1/3 (system hits=2/3)。AIの再定義に言及しているのは良いが、道具の主従性や予測不能性への踏み込みが弱い。特に予測不能性はほぼ言及なし。

**user 確認依頼:** このサンプルは O=3 の典型例として適切か? → [YES / NO / 差し替え提案]

---

#### O = 2 — `q067` (ai_ethics)

**質問:** AIの学習に使われるデータについて、作成者の同意は必要か？

**核心命題:**
- [0] 法的判断は地域で異なる
- [1] 情報的自己決定権が核心
- [2] 異議申立の経路の有無が重要

**回答抜粋:**

```
AIの学習に使われるデータについて、作成者の同意が必要かどうかは、法的、倫理的、
そして実践的な観点から考える必要があります。

**法的観点**: 多くの国では、著作権法やデータ保護法がデータの使用に関する
ガイドラインを提供しています。...
  ... (中略) ...
結論として、AIの学習に使われるデータについて作成者の同意が必要かどうかは、
法的要件を満たしつつ、倫理的配慮を行い、実践的な制約を考慮した上で判断
されるべきです。
```

**選定理由:** 命題カバー 1/3。同意の一般論に偏り、情報的自己決定権・異議申立経路の核心が弱い。

**user 確認依頼:** このサンプルは O=2 の典型例として適切か? → [YES / NO / 差し替え提案]

---

#### O = 1 — `q026` (ugh_theory)

**質問:** PoRの閾値θはどのように設定すべきか？普遍的な値は存在するか？

**核心命題:**
- [0] 普遍的な閾値は存在しない
- [1] タスク・ドメイン・モデルに依存する
- [2] ヒューマンレーティングとの相関から経験的に決定

**回答抜粋:**

```
PoR（Proof of Reserves）の閾値θを設定する際には、いくつかの重要な要素を
考慮する必要があります。PoRは、特に暗号通貨取引所や金融機関が保有する資産の
透明性を確保するための手法として用いられます。...
  ... (中略) ...
普遍的な閾値というものは存在しません。なぜなら、各組織の状況、リスクプロファイル、
法的環境、技術的能力が異なるためです。
```

**選定理由:** 命題カバー 0/3 (system hits=1/3)。PoR を Proof of Reserves と取り違え。普遍閾値否定のみ部分一致。

**user 確認依頼:** このサンプルは O=1 の典型例として適切か? → [YES / NO / 差し替え提案]

---

### 認知バイアス対策（UI 強制事項）

`annotation_ui.py` は以下を自動で実施:

- 順序ランダム化（shuffle + stratified）
- 生成モデル名・ΔE / S / C / f1–f4 / verdict を非表示
- `hits_total` のみ表示（命題カウンタ参考、O への anchoring を避ける軽度情報）
- 1 件 90 秒超で skip 推奨 reminder

## 4. 受入基準（Goal-Driven）

アノテーション作業が完了と言えるのは:

1. accept subset 結合後 **n ≥ 28**（adaptive early stop を許容）
2. `analysis/calibrate_phase_e_thresholds.py` 再実行で fire_rate ∈ [10%, 30%] の
   閾値ペア発見、**または** 再度 no-ship 判定の根拠が `analysis/phase_e_recalibration_result.md` に記録されている
3. ブラインド混入の |Δ| 平均 ≤ 1.0（Likert 換算）、|bias μ_Δ| ≤ 0.5
4. 本ドキュメントのアンカーサンプル 5 件に user 確認印が入っている
5. 結果が `analysis/phase_e_recalibration_result.md` に記録されている

## 5. 手順（user 作業）

### 5.1 候補抽出

```bash
python analysis/annotation_sampler.py --batch-size 15
python analysis/annotation_sampler.py --batch-size 10 --borderline-focus  # Phase 1 用
python analysis/annotation_sampler.py --batch-size 10 --polarity-focus    # Phase 2 用
# 必要なら priority B (orchestrator 生成) を追加
python experiments/orchestrator.py --questions q020-q102 --out gen.jsonl  # 任意
python analysis/annotation_sampler.py --batch-size 15 --orchestrator-jsonl gen.jsonl --append
```

### 5.2 アノテート

```bash
python analysis/annotation_ui.py
# 中断再開:
python analysis/annotation_ui.py --resume
# 任意の再現性チェック:
python analysis/annotation_ui.py --stability-check
```

- batch 区切りで `run_incremental_calibration.py` が自動で回り、
  accept subset n ≥ 28 に達したら full-grid 校正に切り替わり、
  fire_rate ∈ [10%, 30%] のペアが見つかれば STOP 推奨メッセージを出す
- STOP 推奨が出たら user はセッション終了して OK

### 5.3 ブラインド合格判定

```bash
python analysis/annotation_blind_check.py
```

PASS が出れば rubric drift なし。FAIL なら rubric アンカーを再確認してから
`--resume` で続行。

### 5.4 校正と結果記録

```bash
python analysis/merge_ha48_accept40.py --accept-only
python analysis/calibrate_phase_e_thresholds.py
# 結果を analysis/phase_e_recalibration_result.md に記録
# mode_grv.py の _TAU_COLLAPSE_HIGH / _TAU_ANCHOR_LOW を運用値に更新
```

## 6. 関連ファイル

- `analysis/annotation_sampler.py` — 候補抽出
- `analysis/annotation_ui.py` — 対話 CLI
- `analysis/annotation_blind_check.py` — 合格判定
- `analysis/merge_ha48_accept40.py` — 結合
- `analysis/run_incremental_calibration.py` — batch 区切りの暫定校正
- `analysis/calibrate_phase_e_thresholds.py` — 本校正（既存、PR #84 で追加）
- `data/human_annotation_accept40/annotation_accept40.csv` — アノテーション本体
  （`.gitignore` で除外、完走後に commit 判断）

## 7. 非目標（明示）

- LLM-as-judge での O 自動生成（validation premise 破壊のため禁止）
- ΔE / verdict を UI に表示する機能（anchoring 防止）
- HA48 全体の倍増（accept subset のみ拡充で十分）
- production audit.db 抽出（今回スコープ外）
