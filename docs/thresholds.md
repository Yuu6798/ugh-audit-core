# 閾値一覧と導出根拠の索引 (thresholds)

本リポジトリの**パイプライン挙動を調整する (= tunable な) 主要閾値**の
一覧・出典・導出根拠を単一エントリポイントに集約する索引ドキュメント。

## スコープ

**対象 (本書に載せる):**
- verdict / gate 判定閾値 (core pipeline)
- 命題マッチ / 演算子回収の recall / overlap 閾値 (detector)
- cascade matcher / GoldenStore の類似度・gap 閾値
- grv / L_sem / Phase E の校正済み閾値
- 各コンポーネントの重み定数 (S 重み / ΔE 重み / grv 重み等)

**対象外 (本書に載せない):**
- 関数内 severity state transition (例: f2_unknown 内の
  `max_severity < 0.5` — state 更新であって調整用ノブではない)
- 内部合成式の定数 (例: `mode_grv.py` の
  `cover_soft * 0.7 + (1 - drift) * 0.3` — 式自体の定義で、
  単独 tune 対象にならない)
- ハイパラではない制御定数 (例: batch サイズ / ループ上限)

これらの除外対象は各コンポーネント doc および対応するコード行を読むこと。

## 運用ルール

- **数値の定義・計算式・校正方法の詳細は各コンポーネント doc に委ねる**
  (本書は要約と相互リンクのみ)
- tunable 閾値を変更する際は本書と出典 doc の両方を同期させること
- 新規 tunable 閾値を追加する際は本書に必ず 1 行追加する
- 「これは tunable か内部定数か」の判断に迷ったら、設計 doc (`formulas.md`
  / `detector_design.md` 等) で命名されているか、校正スクリプト
  (`analysis/`) で探索対象になっているかを確認する

## 1. 電卓層 (core pipeline)

| 閾値 / 重み | 値 | 出典 doc | コード |
|---|---|---|---|
| verdict `accept` | `ΔE ≤ 0.10` | [`formulas.md`](formulas.md) | `ugh_calculator.py` |
| verdict `rewrite` | `0.10 < ΔE ≤ 0.25` | [`formulas.md`](formulas.md) | `ugh_calculator.py` |
| verdict `regenerate` | `ΔE > 0.25` | [`formulas.md`](formulas.md) | `ugh_calculator.py` |
| verdict `degraded` | `C=None` or `ΔE=None` | [`formulas.md`](formulas.md) | `ugh_calculator.py` |
| S 重み | `f1=5, f2=25, f3=5, f4=5` | [`formulas.md`](formulas.md) | `ugh_calculator.py:WEIGHTS_F` |
| ΔE 重み | `w_s=2, w_c=1` | [`formulas.md`](formulas.md) | `ugh_calculator.py:WEIGHT_S/C` |
| quality_score | `5 - 4 × ΔE` | [`formulas.md`](formulas.md) | `ugh_calculator.py` |
| `C_BIN_THRESHOLDS` | `[0.34, 0.67]` (bin1/2, bin2/3 境界) | `ugh_calculator.py:C_BIN_THRESHOLDS` (設計 doc 未記載、コードが source of truth) | `ugh_calculator.py:C_BIN_THRESHOLDS` |
| `DELTA_E_BIN_THRESHOLDS` | `[0.10, 0.25]` (= verdict 閾値に同期) | [`formulas.md`](formulas.md) §verdict 判定 | `ugh_calculator.py:DELTA_E_BIN_THRESHOLDS` |
| gate_verdict `pass` | `fail_max == 0.0` AND `f4 != None` | [`formulas.md`](formulas.md) §gate_verdict | `ugh_audit/server.py:_gate_verdict_safe` |
| gate_verdict `warn` | `0.0 < fail_max < 1.0` AND `f4 != None` | [`formulas.md`](formulas.md) §gate_verdict | `ugh_audit/server.py:_gate_verdict_safe` |
| gate_verdict `fail` | `fail_max ≥ 1.0` | [`formulas.md`](formulas.md) §gate_verdict | `ugh_audit/server.py:_gate_verdict_safe` |
| gate_verdict `incomplete` | `f4 == None` | [`formulas.md`](formulas.md) §gate_verdict | `ugh_audit/server.py:_gate_verdict_safe` |

**導出根拠**: ΔE の `0.10 / 0.25` は HA48 (n=48, ρ=-0.5195) で校正済み
確定値。詳細は [`validation.md`](validation.md) の HA48 verdict 単調性
(`accept(3.44) > rewrite(2.62) > regenerate(1.00)`) を参照。gate_verdict
は f1〜f4 の max を見る構造的健全性ゲート (`fail_max = max(f1, f2, f3, f4)`)
で、`verdict` とは独立に算出される副次出力。`C_BIN_THRESHOLDS` は
`ugh_calculator.py` のみで定義され設計 doc には未記載のため、変更時は
コード定数と本書の両方を同期させる (formulas.md 側にも記載する場合は
新たに 1 節追加が必要)。

## 2. 検出層 (detector)

| 閾値 | 値 | 出典 | コード |
|---|---|---|---|
| 命題マッチ `direct_recall` | `≥ 0.15` | [`detector_design.md`](detector_design.md) | `detector.py:1080` |
| 命題マッチ `full_recall` (fr) | `≥ 0.30` | [`detector_design.md`](detector_design.md) | `detector.py:1080` |
| 命題マッチ `overlap` | `≥ min(_MIN_OVERLAP, len(prop_bigrams))` — 短命題で `_MIN_OVERLAP` 未満でも通る | [`detector_design.md`](detector_design.md) | `detector.py:1079-1080` |
| 演算子回収 `direct_recall` | `≥ 0.10` | [`detector_design.md`](detector_design.md) | `detector.py` |
| 演算子回収 `full_recall` | `≥ 0.25` | [`detector_design.md`](detector_design.md) | `detector.py` |
| 演算子回収 `overlap` | `≥ 2` | [`detector_design.md`](detector_design.md) | `detector.py` |
| Relaxed Tier1 ΔE ゲート (`_RELAXED_DELTA_E_MAX`) | `≤ 0.04` | [`detector_design.md`](detector_design.md) §Relaxed Tier1 | `detector.py:_RELAXED_DELTA_E_MAX` |
| Relaxed Tier1 operator-family branch | `direct≥0.15 / full≥0.35 / overlap≥_MIN_OVERLAP` (`operator_family is not None` 時に size 判定より優先) | [`detector_design.md`](detector_design.md) §Relaxed Tier1 | `detector.py:_relaxed_thresholds:947-956` |
| Relaxed Tier1 size≥8 bg | `direct≥0.10 / full≥0.30 / overlap≥2` | [`detector_design.md`](detector_design.md) §Relaxed Tier1 | `detector.py:_RELAXED_BY_SIZE` |
| Relaxed Tier1 size≥5 bg | `direct≥0.12 / full≥0.30 / overlap≥2` | [`detector_design.md`](detector_design.md) §Relaxed Tier1 | `detector.py:_RELAXED_BY_SIZE` |
| Relaxed Tier1 fallback | `direct≥0.15 / full≥0.35 / overlap≥_MIN_OVERLAP` (size 条件が全て不発時) | [`detector_design.md`](detector_design.md) §Relaxed Tier1 | `detector.py:_relaxed_thresholds` |
| Relaxed Tier1 overlap 短命題ケア | `≥ min(overlap_t, len(prop_bigrams))` — 通常マッチと同じ size-cap 適用 | [`detector_design.md`](detector_design.md) | `detector.py:1143-1144` |
| 命題マッチ最小 overlap (`_MIN_OVERLAP`) | `3` (基準値、短命題では `min(3, len(prop_bigrams))` で上限される) | [`detector_design.md`](detector_design.md) | `detector.py:_MIN_OVERLAP` |
| 命題マッチ 緩和帯ガード (full_recall 0.30–0.35 帯) | `< 0.35` で relaxed tier 流用の厳格検証を発動 | [`detector_design.md`](detector_design.md) | `detector.py:1092` |
| `check_f1_anchor` coverage 重度 `1.0` | `coverage < 0.3` | [`detector_design.md`](detector_design.md) | `detector.py:300-303` |
| `check_f1_anchor` coverage 重度 `0.5` | `0.3 ≤ coverage < 0.6` | [`detector_design.md`](detector_design.md) | `detector.py:300-303` |
| `check_f4_premise` 安全語彙密度 高 | `density ≥ 0.6` AND substantive ≤ 2 → f4=1.0 | [`detector_design.md`](detector_design.md) | `detector.py:633` |
| `check_f4_premise` 安全語彙密度 中 | `density ≥ 0.4` AND substantive ≤ 2 → f4=0.5 | [`detector_design.md`](detector_design.md) | `detector.py:635` |

**導出根拠**: 命題マッチの 3 閾値はもともと `0.15 / 0.35 / 3` 時代から
`fr 0.30` に緩和された経緯あり ([`detector_design.md`](detector_design.md))。
overlap は `_MIN_OVERLAP=3` が基準値だが、命題 bigram 数が 3 未満の
短命題では `min(_MIN_OVERLAP, len(prop_bigrams))` で上限され、相対的に
機能する (非整合を避けるためのサイズ適応)。Relaxed Tier1 の閾値選択は
`_relaxed_thresholds()` が決定木で行う: 演算子命題は operator-family
branch (`0.15/0.35/3`) を最優先、それ以外は `_RELAXED_BY_SIZE` タプル
(命題 bigram 数に応じた段階的緩和)、全て不発なら fallback
(`0.15/0.35/3`) に落ちる。`full_recall < 0.35` 緩和帯ガードは
`0.30 ≤ fr < 0.35` で通過した命題に対し、relaxed tier より厳格な
文レベル接地 + 汎用チャンクフィルタを適用して偽陽性を抑える追加防衛線。
f1_anchor coverage ゲート (< 0.3 / < 0.6) と f4_premise 安全語彙密度
(≥ 0.4 / ≥ 0.6) は構造ゲートの段階的重度付けに使用。
実験スクリプト: `analysis/threshold_validation/run_proposition_hit_experiment.py`

## 3. Cascade Matcher (tier 2/3)

| 閾値 | 値 | 出典 | コード |
|---|---|---|---|
| `θ_sbert` (cosine 閾値) | `0.50` | [`cascade_design.md`](cascade_design.md) §チューニング | `cascade_matcher.py:THETA_SBERT` |
| `δ_gap` (top1 - top2) | `0.04` | [`cascade_design.md`](cascade_design.md) | `cascade_matcher.py:DELTA_GAP` |
| `HIGH_SCORE_THRESHOLD` | `0.70` | [`cascade_design.md`](cascade_design.md) §c4 閾値緩和 | `cascade_matcher.py` |
| `RELAXED_DELTA_GAP` | `0.02` | [`cascade_design.md`](cascade_design.md) §c4 閾値緩和 | `cascade_matcher.py` |

**導出根拠**: `cascade_design.md` に感度分析表あり (θ=0.45–0.55 で同等性能、
中間値 0.50 採用)。`δ=0.04` で false_rescue=0 を達成、`δ=0.03` では 1 件漏れ。

## 4. GoldenStore リファレンス検索

| 閾値 | 値 | 出典 | コード |
|---|---|---|---|
| Stage 2 bigram `min_score` | `0.1` | [`golden_store.md`](golden_store.md) | `ugh_audit/reference/golden_store.py:_BIGRAM_MIN_JACCARD` |
| Stage 2 top_K | `5` | [`golden_store.md`](golden_store.md) | `_BIGRAM_CANDIDATE_TOP_K` |
| Stage 3 SBert `δ_gap` | `0.04` | [`golden_store.md`](golden_store.md) | `_SBERT_GAP_DELTA` (cascade と同期) |
| Stage 3 `HIGH_SCORE` | `0.70` | [`golden_store.md`](golden_store.md) | `_SBERT_HIGH_SCORE` |
| Stage 3 `relaxed_δ_gap` | `0.02` | [`golden_store.md`](golden_store.md) | `_SBERT_RELAXED_GAP` |

**注**: cascade_matcher の値と同期運用。どちらか変更時は両方追従させること。

## 5. grv (因果構造損失)

| 閾値 / 重み | 値 | 出典 | コード |
|---|---|---|---|
| 合成重み `w_d / w_s / w_c` | `0.70 / 0.05 / 0.25` | [`grv_design.md`](grv_design.md) | `grv_calculator.py:W_DRIFT/DISPERSION/COLLAPSE_V2` |
| タグ `high_gravity` | `grv ≥ 0.30` | [`grv_design.md`](grv_design.md) §タグ閾値 | `grv_calculator.py:TAG_HIGH` |
| タグ `mid_gravity` | `grv ≥ 0.20` | [`grv_design.md`](grv_design.md) | `grv_calculator.py:TAG_MID` |
| 参照重心 manual 重み | `w_q=0.60, w_m=0.40` | [`grv_design.md`](grv_design.md) §参照重心 | `grv_calculator.py:_REF_WEIGHTS` |
| 参照重心 auto 重み | `w_q=0.80, w_m=0.20` | [`grv_design.md`](grv_design.md) | 同上 |
| 参照重心 missing | `w_q=1.00, w_m=0.00` | [`grv_design.md`](grv_design.md) | 同上 |

**導出根拠**: HA48 で ρ=-0.357 (σ=0.051)。タグ閾値は HA48 分布
(mean=0.185, σ=0.051, range=[0.10, 0.31]) を校正した値
([`grv_design.md`](grv_design.md) §タグ閾値)。

## 6. L_sem (意味損失関数)

`DEFAULT_WEIGHTS` 全 7 項 (`semantic_loss.py:39-47`)。**full-sample
最適値**ではなく **LOO-CV 補正後の runtime 値** を記載する (両者は異なる)。
出典は runtime コード (`semantic_loss.py`) を single source of truth とする
— 後述のとおり設計 doc 側に既知の stale 値があるため。

| 閾値 / 重み | 値 | 出典 | コード |
|---|---|---|---|
| `DEFAULT_WEIGHTS["L_P"]` | `0.27` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_Q"]` | `0.02` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_R"]` | `0.03` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_A"]` | `0.02` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_G"]` | `0.35` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_F"]` | `0.21` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |
| `DEFAULT_WEIGHTS["L_X"]` | `0.10` | `semantic_loss.py:34-47` | `semantic_loss.py:DEFAULT_WEIGHTS` |

**導出根拠**: HA48 Phase 5 の full-sample 3 項最適化で
`L_P=0.425 / L_F=0.275 / L_G=0.850` が ρ=-0.6020 を達成
([`validation.md`](validation.md))。ただし LOO-CV で shrinkage=0.128
(n=48 で不安定) が検出されたため、LOO mean 比率
`L_P:L_F:L_G ≈ 0.41:0.30:0.91` を正規化して保守的に配分した結果が
runtime の `L_P=0.27 / L_F=0.21 / L_G=0.35` (`semantic_loss.py:34-38`
コメント参照)。`L_X=0.10` は `L_G` 削減分の一部を極性反転検出に
再配分。`L_Q / L_R / L_A` は HA48 で有意信号なしだが理論的保持
(低重みで運用)。

### 設計 doc 側の記述との同期

`semantic_loss.py:DEFAULT_WEIGHTS` を single source of truth として以下の
設計 doc と同期済み (L_sem weight sync PR 以降):

- `docs/semantic_loss.md:46-60` および `:372-386` (デフォルト重み表 2 箇所)
- `docs/validation.md:99-115` (Phase 5 確定 DEFAULT_WEIGHTS Python block)
- `docs/grv_design.md:141-146` (L_sem との接続パラグラフ)

各 doc は LOO-CV 補正の経緯 (full-sample 最適 `L_G=0.85` → LOO mean
比率で `0.35`) も記載済み。今後 runtime を変更する際は本書と上記 4 箇所を
同時更新すること。

## 7. Phase E verdict_advisory (mode_conditioned_grv)

| 閾値 | 値 | 出典 | コード |
|---|---|---|---|
| `τ_collapse_high` | `0.28` | [`phase_e_verdict_integration.md`](phase_e_verdict_integration.md) | `mode_grv.py:_TAU_COLLAPSE_HIGH` |
| `τ_anchor_low` | `0.80` | [`phase_e_verdict_integration.md`](phase_e_verdict_integration.md) | `mode_grv.py:_TAU_ANCHOR_LOW` |

**導出根拠**: n=63 (HA48 + accept40 batch1) でグリッド探索
`{0.20..0.40}×{0.60..0.80}` step=0.02。採用値で
`rho_primary_full=0.4408 → rho_advisory_full=0.5225`, `fire_rate=0.225`。
詳細: `analysis/phase_e_calibration_result.md`, `analysis/phase_e_calibration_grid.csv`。

## 8. レガシー (現在未使用 / 廃止済み)

本パイプラインで**参照されていない**が、コード上に残っている閾値群。
削除時の安全性確認用に列挙する。

| 閾値 | 値 | 場所 | 状態 |
|---|---|---|---|
| `_POR_FIRE_THRESHOLD` | `0.82` | `ugh_audit/engine/runtime.py:77` | レガシー互換層、メインパイプライン未使用 ([CLAUDE.md](../CLAUDE.md) §Important Notes) |
| 旧 ΔE bin | `0.02 / 0.12 / 0.35` | `engine/runtime.py:_LEGACY_BIN*` | HA48 校正で `0.10 / 0.25` に統一済み |
| 旧 grv タグ | `0.33 / 0.66` | — | HA48 分布校正で `0.20 / 0.30` に差し替え済み |

## 9. 検証データの相関値 (参考)

本書は閾値索引が本分のため、ρ (Spearman 相関) の完全一覧は
[`validation.md`](validation.md) に委ねる。主要 anchor のみ再掲:

| 指標 | n | ρ | 備考 |
|---|---|---|---|
| ΔE (system C) | 48 | -0.5195 | HA48 主評価、デプロイ可能指標 |
| ΔE (human C) | 48 | +0.8616 | 参照上限 |
| L_sem Phase 5 | 48 | -0.6020 | L_P+L_F+L_G 3 項統合 |
| grv | 48 | -0.357 | σ=0.051 |
| anchor_alignment | 48 | +0.4063 | p=0.004 |
| collapse_risk | 48 | -0.3191 | p=0.027 |
| rho_advisory_full | 63 | +0.5225 | Phase E verdict_advisory |

## 10. 変更時チェックリスト

閾値を変更する場合は以下を実施する:

1. [ ] 本書 (この doc) の該当行を更新
2. [ ] 出典 doc (該当コンポーネント doc) の数値と整合確認
3. [ ] `CLAUDE.md` の Key Thresholds 表に載っている閾値は、そちらも更新
4. [ ] 閾値の根拠が `validation.md` や `analysis/` の校正結果にある場合は、
      再校正が必要か判断
5. [ ] `ruff check .` と `pytest -q` が緑になることを確認
6. [ ] PR description で変更前後の値と `ρ` への影響を明記

## 11. 関連ドキュメント

| doc | 扱う閾値 |
|---|---|
| [`formulas.md`](formulas.md) | verdict / S 重み / ΔE 重み / quality_score |
| [`detector_design.md`](detector_design.md) | 命題マッチ / 演算子回収 / Relaxed Tier1 |
| [`cascade_design.md`](cascade_design.md) | θ_sbert / δ_gap / HIGH_SCORE / RELAXED |
| [`golden_store.md`](golden_store.md) | Stage 2 bigram / Stage 3 SBert |
| [`grv_design.md`](grv_design.md) | grv 重み / タグ閾値 / 参照重心 |
| [`semantic_loss.md`](semantic_loss.md) | L_sem 項別重み |
| [`phase_e_verdict_integration.md`](phase_e_verdict_integration.md) | τ_collapse / τ_anchor |
| [`validation.md`](validation.md) | HA48 / HA20 ρ 検証結果全般 |
