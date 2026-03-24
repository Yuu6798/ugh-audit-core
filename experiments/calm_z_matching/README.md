# CALM z-vector 命題照合実験（2026-03）

## 結論

z照合は hit率を 0.58 → 0.85 に改善するが、
弁別力が ρ=0.909 → 0.426 に低下する。
単体での置き換えは不可。セカンドオピニオンとしての併用が有望。

### 数値サマリー

| 能力 | tfidf (A) | CALM z (C) |
|------|-----------|------------|
| 命題 hit率 | 0.58 | **0.85** |
| human_score 相関 (ρ) | **0.909** | 0.426 |
| zero-recall 件数 | 2 | 1 |

### 主要発見

- 日本語 z空間の類似度レンジが狭い（関連ペア 0.4-0.6 vs 無関係 0.2-0.4）
- 英日クロスリンガルは直交（cos ≈ -0.04）— z空間は完全モノリンガル
- 閾値 θ=0.50 に収束し、過剰マッチの根本原因に

## 次のアクション

- [ ] tfidf miss × z hit の 27% を手動分類（真の改善 vs 偽陽性）
- [ ] tfidf + z + structural_gate の3特徴量回帰（n=20→48 拡張後）
- [ ] hit/miss 二値判定をやめて z類似度を連続値で使う回帰モデルの検証

## 再現手順

```bash
cd experiments/calm_z_matching

# 1. 入力データ生成（data/ から結合）
python prepare_data.py

# 2. 実験実行
python run_experiment.py [--device cpu|cuda] [--skip-calm] [--skip-sbert]

# 3. z品質チェック（オプション）
python z_quality_check.py
```

## ファイル構成

| ファイル | 用途 |
|---------|------|
| `results_summary.md` | 結果サマリー（数値・考察・技術メモ） |
| `results_detail.csv` | 20件×3手法の照合結果（次の回帰実験の入力） |
| `run_experiment.py` | 再現用スクリプト（Phase 1-4） |
| `prepare_data.py` | 入力データ生成（`data/` から結合） |
| `z_quality_check.py` | z空間の品質検証スクリプト |
| `calm_encoder/` | CALM Autoencoder のローカル定義 |

`experiment_input.json` は `prepare_data.py` で再生成可能なため、リポジトリには含めない。
