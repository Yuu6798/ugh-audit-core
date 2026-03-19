# data/ — Phase C 収集データ

このディレクトリには UGH Audit Phase C の収集データと採点結果を保存します。

## ディレクトリ構成

```
data/
├── phase_c_v0/                         # v0: 初回収集・初回採点
│   ├── phase_c_raw.jsonl               # GPT-4o 生回答（不変・原始データ）
│   ├── phase_c_results.csv             # v0採点結果（ST backend / reference_core基準）
│   ├── phase_c_report.html             # v0 HTMLレポート
│   └── calibration_notes.md           # v0で発見された問題の記録
│
└── phase_c_v1/                         # v1: キャリブレーション後（作業中）
    ├── phase_c_results_refull.csv      # reference全文でのΔE再計算
    ├── phase_c_results_summary.csv     # 要約比較でのΔE再計算
    ├── human_annotations_20.csv        # 人手アノテーション20件
    └── threshold_calibration.md       # 新閾値の根拠
```

## データ仕様

### phase_c_raw.jsonl（原始データ）

- **絶対に上書きしない**
- モデル: `gpt-4o`
- 問題数: 102問 × 3温度（0.0 / 0.7 / 1.0）= 306件
- 収集日: 2026-03-19

フィールド:
| フィールド | 型 | 説明 |
|-----------|-----|------|
| `id` | str | 問題ID（q001〜q100, qg01/qg02） |
| `category` | str | カテゴリ（ugh_theory / technical_ai / ai_philosophy / ai_ethics / epistemology / adversarial） |
| `role` | str | 問題の役割（test / baseline / grv_calibration） |
| `difficulty` | str | 難易度（easy / medium / hard） |
| `temperature` | float | 生成温度（0.0 / 0.7 / 1.0） |
| `question` | str | 質問文 |
| `response` | str | GPT-4o の回答 |
| `reference` | str | 理想回答（全文） |
| `reference_core` | str | 核心文（1文・40字以内） |
| `trap_type` | str | 罠の種類 |
| `requires_manual_review` | bool | 人手確認フラグ |
| `model` | str | 使用モデル名 |
| `usage` | dict | トークン使用量 |

### phase_c_results.csv（採点結果）

追加フィールド:
| フィールド | 型 | 説明 |
|-----------|-----|------|
| `por` | float | PoR スコア（0〜1） |
| `delta_e` | float | ΔE スコア（0〜1） |
| `por_fired` | bool | PoR発火（≥0.82） |
| `meaning_drift` | str | ΔEラベル（同一意味圏 / 意味乖離等） |
| `dominant_gravity` | str | grv上位語 |

## バージョン管理方針

- 生回答（`phase_c_raw.jsonl`）は **v0のみ**。再収集時は `phase_c_v1/phase_c_raw_v1.jsonl` として別保存
- 採点結果はバージョンごとにディレクトリを分ける
- `calibration_notes.md` に各バージョンの発見・問題・決定を記録する
