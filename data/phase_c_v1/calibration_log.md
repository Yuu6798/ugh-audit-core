# Phase C v1 — calibration log

**更新日**: 2026-03-21  
**backend**: sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`)  
**採点基準**: `reference_core`  
**モデル**: gpt-4o  
**問題数**: 102問 × 3温度 = 306件

---

## v1再採点結果サマリー（temp=0.0 代表値）

| 指標 | 値 |
|------|-----|
| PoR 平均 | 0.800 |
| PoR 発火率（≥0.82） | 48% (49/102件) |
| ΔE 平均 | 0.516 |
| ΔE ラベル分布 | 全件「意味乖離」(100%) |
| dominant_gravity 1位 | 「モデル」(12件) |

---

## 補足

- 本v1は `scripts/rescore_phase_c.py` を用いて、`sentence-transformers` バックエンドで再採点・再出力した成果物を保存する。
- 生成ファイル:
  - `phase_c_scored_v1.jsonl`
  - `phase_c_v1_results.csv`
  - `phase_c_report_v1.html`
- ΔEの評価軸は現時点では `reference_core` のまま。`reference` 全文比較や要約比較は未反映。

---

## 現時点の解釈

- PoR平均と発火率は v0 サマリーと同水準で、STバックエンド上の再現性は保たれている。
- 一方で ΔE は temp=0.0 の代表値で全件「意味乖離」のままであり、短い `reference_core` と長文回答の非対称性が依然として強く疑われる。
- grv は日本語トークナイズ修正の影響を受け、語彙分布の品質が更新されている。
