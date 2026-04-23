# Baseline Comparison — UGHer vs BLEU / BERTScore / SBert

HA48 / HA20 の (response, reference, human O) トリプルに対し、
UGHer (ΔE ベース) と 3 baseline 指標の Spearman ρ(metric, O) を比較。

**向きの統一:**
- BLEU / BERTScore / SBert cos: 高 similarity = 良回答 → **正相関期待**
- UGHer は `1 - ΔE_full` (similarity 向き) で比較 → **正相関期待**

**計算式 (CI):** Fisher z — `tanh(atanh(ρ) ± 1.96/sqrt(n-3))`

## HA20 (n=20)

### O スコアとの相関 (個別)

| 指標 | n_valid | Spearman ρ | p | 95% CI |
|---|---|---|---|---|
| BLEU | 20 | +0.4924 | 0.0274 | [+0.0637, +0.7676] |
| BERTScore_F1 | 20 | +0.5557 | 0.0110 | [+0.1500, +0.8012] |
| SBert_cos | 20 | +0.4283 | 0.0596 | [-0.0176, +0.7321] |
| UGHer_1mdE | 20 | +0.7696 | 0.0001 | [+0.4959, +0.9042] |

### UGHer vs baseline: Steiger's Z (dependent correlations)

同一サンプル上で 2 指標が共通の O スコアとの相関でどれだけ差があるかを検定。Δρ > 0 は UGHer が高い点推定。p < 0.05 が統計的有意。

| vs baseline | n | ρ(UGHer,O) | ρ(base,O) | Δρ | ρ(UGHer,base) | Steiger Z | p | sig(α=0.05) |
|---|---|---|---|---|---|---|---|---|
| BLEU | 20 | +0.7696 | +0.4924 | +0.2772 | +0.3192 | +1.502 | 0.1330 | no |
| BERTScore_F1 | 20 | +0.7696 | +0.5557 | +0.2139 | +0.2676 | +1.189 | 0.2343 | no |
| SBert_cos | 20 | +0.7696 | +0.4283 | +0.3413 | +0.2987 | +1.759 | 0.0785 | no |

## HA48 (n=48)

### O スコアとの相関 (個別)

| 指標 | n_valid | Spearman ρ | p | 95% CI |
|---|---|---|---|---|
| BLEU | 48 | +0.3180 | 0.0276 | [+0.0373, +0.5523] |
| BERTScore_F1 | 48 | +0.3312 | 0.0215 | [+0.0520, +0.5624] |
| SBert_cos | 48 | +0.2607 | 0.0735 | [-0.0253, +0.5072] |
| UGHer_1mdE | 48 | +0.4817 | 0.0005 | [+0.2290, +0.6737] |

### UGHer vs baseline: Steiger's Z (dependent correlations)

同一サンプル上で 2 指標が共通の O スコアとの相関でどれだけ差があるかを検定。Δρ > 0 は UGHer が高い点推定。p < 0.05 が統計的有意。

| vs baseline | n | ρ(UGHer,O) | ρ(base,O) | Δρ | ρ(UGHer,base) | Steiger Z | p | sig(α=0.05) |
|---|---|---|---|---|---|---|---|---|
| BLEU | 48 | +0.4817 | +0.3180 | +0.1637 | +0.1851 | +0.984 | 0.3252 | no |
| BERTScore_F1 | 48 | +0.4817 | +0.3312 | +0.1505 | +0.1893 | +0.910 | 0.3628 | no |
| SBert_cos | 48 | +0.4817 | +0.2607 | +0.2211 | +0.2715 | +1.380 | 0.1677 | no |

## 解釈

- 本表は「UGHer が既存 baseline に対してどの程度強い信号を示すか」の
  直接比較を提供する。個別 CI overlap が大きければ 3 指標は統計的に区別
  困難、Steiger's Z が p<0.05 なら Δρ は有意な差と判定される。
- 現状 `docs/validation.md §Limitations` に記載した「ベースライン比較の
  不在」を本 script で埋める。n=48 / n=20 の検出力は低く、Δρ が
  medium effect size (0.15–0.20 程度) でも Steiger's Z で有意差を検出
  するには n≥100 程度の拡張が必要と予想される。
- 査読で「UGHer > baseline が統計有意に成立」と主張するには Steiger's Z
  p<0.05 が前提。現 n で非有意なら「点推定では全方位優位だが統計有意性は
  n 拡張後に確定」と正直に報告する。

## 再現

```bash
# self-contained な [baseline] extras で全依存 (bert-score /
# sacrebleu / sentence-transformers / scipy) を一括導入
pip install -e ".[baseline]"
python analysis/baseline_comparison.py
```

個別 pip を使う場合:

```bash
pip install bert-score sacrebleu sentence-transformers scipy
```

出力: `baseline_comparison_ha{48,20}.csv`, `baseline_comparison_summary.md`