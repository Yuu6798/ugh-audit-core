# Baseline Comparison — UGHer vs BLEU / BERTScore / SBert

HA48 / HA20 の (response, reference, human O) トリプルに対し、
UGHer (ΔE ベース) と 3 baseline 指標の Spearman ρ(metric, O) を比較。

**向きの統一:**
- BLEU / BERTScore / SBert cos: 高 similarity = 良回答 → **正相関期待**
- UGHer は `1 - ΔE_full` (similarity 向き) で比較 → **正相関期待**

**計算式 (CI):** Fisher z — `tanh(atanh(ρ) ± 1.96/sqrt(n-3))`

## HA20 (n=20)

| 指標 | n_valid | Spearman ρ | p | 95% CI |
|---|---|---|---|---|
| BLEU | 20 | +0.4924 | 0.0274 | [+0.0637, +0.7676] |
| BERTScore_F1 | 20 | +0.5557 | 0.0110 | [+0.1500, +0.8012] |
| SBert_cos | 20 | +0.4283 | 0.0596 | [-0.0176, +0.7321] |
| UGHer_1mdE | 20 | +0.7696 | 0.0001 | [+0.4959, +0.9042] |

## HA48 (n=48)

| 指標 | n_valid | Spearman ρ | p | 95% CI |
|---|---|---|---|---|
| BLEU | 48 | +0.3180 | 0.0276 | [+0.0373, +0.5523] |
| BERTScore_F1 | 48 | +0.3312 | 0.0215 | [+0.0520, +0.5624] |
| SBert_cos | 48 | +0.2607 | 0.0735 | [-0.0253, +0.5072] |
| UGHer_1mdE | 48 | +0.4817 | 0.0005 | [+0.2290, +0.6737] |

## 解釈

- 本表は「UGHer が既存 baseline に対してどの程度強い信号を示すか」の
  直接比較を提供する。CI overlap が大きければ 3 指標は統計的に区別
  困難、overlap が小さければ UGHer の優位性が検証される。
- 現状 `docs/validation.md §Limitations` に記載した「ベースライン比較の
  不在」を本 script で埋める。n=48 / n=20 の CI 幅は広いため、結論は
  「点推定の順位 + CI overlap の定量開示」に留める。

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