# Model C' 実装設計 — ボトルネック型 quality_score

## 概要

Model C'（ボトルネック型）を `detector.py` に `compute_quality_score()` として実装する。
既存の `propositions_hit_rate` は変更しない。`quality_score` は追加フィールドとして提供する。

## パラメータ（暫定値）

| 定数名 | 値 | 備考 |
|--------|-----|------|
| QUALITY_ALPHA | 0.4 | n=20 LOO-CV 検証済み (ρ=0.8018)。n=48 で再校正予定 |
| QUALITY_BETA | 0.0 | 全 LOO fold で 0.0（ボトルネック専用パス） |
| QUALITY_GAMMA | 0.8 | n=20 LOO-CV 検証済み。n=48 で再校正予定 |
| QUALITY_MODEL_NAME | "bottleneck_v1" | モデル識別子 |

## 関数仕様

```python
def compute_quality_score(
    propositions_hit_rate: float,
    fail_max: float | None,
    delta_e_full: float,
    alpha: float = QUALITY_ALPHA,
    beta: float = QUALITY_BETA,
    gamma: float = QUALITY_GAMMA,
) -> dict:
```

### 入力

| 引数 | 型 | ソース | 備考 |
|------|-----|--------|------|
| propositions_hit_rate | float | Evidence.propositions_hit / Evidence.propositions_total | 0.0〜1.0 |
| fail_max | float or None | structural_gate の max(f1,f2,f3,f4) | None → 0.0 にフォールバック |
| delta_e_full | float | AuditResult.delta_e_full | 0.0〜1.0 |
| alpha, beta, gamma | float | デフォルト = 定数値 | オーバーライド可 |

### 出力

```python
{
    "quality_score": float,         # 1.0〜5.0
    "quality_model": str,           # "bottleneck_v1"
    "quality_params": dict,         # {"alpha": 0.4, "beta": 0.0, "gamma": 0.8}
    "quality_loss_breakdown": dict  # {"L_P": float, "L_struct": float, "L_R": float, "L_op": float}
}
```

## 計算ロジック

```
L_P = 1 - propositions_hit_rate
L_struct = fail_max if fail_max is not None else 0.0
L_R = delta_e_full
L_linear = α × L_P + β × L_struct + γ × L_R
L_op = max(L_struct, L_linear)
quality_score = clamp(5 - 4 × L_op, 1.0, 5.0)
```

## フォールバック仕様

| 条件 | 挙動 |
|------|------|
| fail_max is None | L_struct = 0.0。ボトルネック不発動、L_linear のみ |
| delta_e_full 欠損 (呼び出し側で 0.0 を渡す) | L_R = 0.0 |
| 全入力正常 | 通常の Model C' 算出 |

## モジュール依存関係

```
detector.py
  ├── cascade_matcher       → propositions_hit_rate（既存・変更なし）
  ├── structural_gate       → fail_max（既存・変更なし）
  ├── delta_e               → delta_e_full（既存・変更なし）
  └── compute_quality_score → quality_score（新規追加）
        入力: propositions_hit_rate, fail_max, delta_e_full
        出力: quality_score + メタ情報
        依存: なし（純粋関数、外部状態を参照しない）
```

## 呼び出し箇所

`detect()` 関数の末尾、`return Evidence(...)` の直前には追加しない。
理由: `detect()` は Evidence（frozen dataclass）を返し、fail_max や delta_e_full は
detect() のスコープ外で算出される。

`compute_quality_score()` は独立関数としてエクスポートし、
パイプライン（batch_audit_102.py 等）が全入力を揃えた時点で呼び出す。

## 検証結果サマリ

- 全データ ρ: 0.8292 (Model A: 0.4030)
- LOO-CV ρ: 0.8018 (受理基準 0.65 をクリア)
- hard_negative 誤影響: 0 件
- 判定: **GO**
