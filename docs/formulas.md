# UGH 計算式 — PoR / ΔE / quality_score / verdict

電卓層 (`ugh_calculator.py`) で算出される数値指標の定義。
本ドキュメントが扱うのは **core pipeline** (detector tier 1 + calculator + decider) であり、同じ入力に対して決定的に同じ出力を返す。

cascade layer (SBert ベースの tier 2/3) は core pipeline に対する
optional な確率的回収補強で、C の上方修正のみを行う（降格はしない）。
cascade の有無は本書の計算式自体を変えない。2 層の関係は
[`cascade_design.md § core pipeline と cascade layer の関係`](cascade_design.md#core-vs-cascade)
を参照。

## 計算式一覧

```
PoR = (S, C)

S = 1 - Σ(w_k × f_k) / Σ(w_k)
    w = {f1: 5, f2: 25, f3: 5, f4: 5}    デフォルト Σ(w_k) = 40
    f4=None 時: f4 の重み（5）を除外し Σ(w_k) = 35 で計算

C = hits / n_propositions
    n_propositions=0（未提供）時: C=None（計算不能）

ΔE = (w_s × (1-S)² + w_c × (1-C)²) / (w_s + w_c)
    w_s = 2, w_c = 1
    C=None 時: ΔE=None（算出不可）

quality_score = 5 - 4 × ΔE    # パラメータフリー [1,5]
    ΔE=None 時: quality_score=None
```

## 各式の意味

| 指標 | 意味 |
|---|---|
| **S** | f1〜f4 の加重平均による構造完全性。f2 (用語捏造) に最大重み 25 を配置。f4=None 時は重み除外 |
| **C** | 命題照合 (tfidf + cascade) による核心カバレッジ。命題未提供時は None |
| **ΔE** | S と C の加重二乗和。両軸からの距離を 1 つのスカラーに統合。C=None 時は算出不可 |
| **quality_score** | ΔE の線形変換。ΔE=0 → 5.0, ΔE=1 → 1.0 |

## verdict 判定（HA48 検証済み確定値）

| verdict | 条件 | 意味 |
|---------|------|------|
| accept | C≠None AND ΔE ≤ 0.10 | 意味的に十分な回答 |
| rewrite | C≠None AND 0.10 < ΔE ≤ 0.25 | 部分的な修正で改善可能 |
| regenerate | C≠None AND ΔE > 0.25 | 再生成が必要 |
| degraded | C=None OR ΔE=None | メタデータ不足で本計算不能 |

## gate_verdict (構造ゲート)

| gate_verdict | 条件 | 意味 |
|---|---|---|
| pass | fail_max == 0.0 AND f4 ≠ None | 構造完全 |
| warn | 0.0 < fail_max < 1.0 AND f4 ≠ None | 部分的な構造欠陥 |
| fail | fail_max ≥ 1.0 | 構造的に破綻 |
| incomplete | f4 == None | f4 未計算 |

## 拡張: 意味損失関数 L_sem

`semantic_loss.py` は ΔE を破壊せず損失を 7 項に分解する診断用ラッパー。
詳細は [`semantic_loss.md`](semantic_loss.md) 参照。

## 検証

HA48 (n=48) での検証結果は [`validation.md`](validation.md) 参照。
