# 意味損失関数 $L_{\text{sem}}$ — 仮定方程式と既存パイプラインとの対応

## 概要

AI 回答の意味的誠実性を定量評価するための理論的枠組みとして、
テキスト変換における **意味損失関数** $L_{\text{sem}}$ を定義する。

現行パイプラインの PoR 座標 $(S, C)$ と $\Delta E$ は、
この損失関数の特殊ケースとして位置付けられる。

## 意味表現

文脈 $c$ の下で、テキスト $x$ の意味表現を 5 つ組で定義する:

$$
\Phi(x \mid c) = (P_x,\; Q_x,\; R_x,\; G_x,\; A_x)
$$

| 記号 | 名称 | 定義 |
|------|------|------|
| $P_x$ | 命題集合 | テキストが主張する命題の集合 |
| $Q_x$ | 制約集合 | 命題に付随する極性・様態・当為などの修飾制約 |
| $R_x$ | 参照集合 | テキストが前提とする外部参照・定義 |
| $G_x$ | 因果・依存グラフ | 命題間の因果関係・論理的依存構造 |
| $A_x$ | 曖昧性量 | テキストの解釈に残る不確実性 |

## 意味損失関数

元文 $s$、変換後 $t$ に対する意味損失関数:

$$
L_{\text{sem}}(s, t \mid c)
= \alpha L_P + \beta L_Q + \gamma L_R + \delta L_G + \epsilon L_A + \zeta L_X
$$

$$
\alpha, \beta, \gamma, \delta, \epsilon, \zeta \geq 0, \qquad
\alpha + \beta + \gamma + \delta + \epsilon + \zeta = 1
$$

### 各項の定義

#### $L_P$ — 命題損失

$$
L_P = 1 - \frac{1}{|P_s|} \sum_{p \in P_s} \max_{p' \in P_t} \mathrm{sim}_P(p, p')
$$

元文の命題が変換後にどれだけ保存されたか。
$|P_s| = 0$ のとき $L_P$ は未定義（degraded）。

#### $L_Q$ — 制約損失

$$
L_Q = \frac{
  \sum_{q \in Q_s} w(q) \left(1 - \max_{q' \in Q_t} \mathrm{sim}_Q(q, q')\right)
}{
  \sum_{q \in Q_s} w(q)
}
$$

命題に付随する制約（極性、様態、当為など）の加重保存率。
$w(q)$ は制約の重要度。

#### $L_R$ — 参照安定性損失

$$
L_R = 1 - \frac{1}{|R_s|} \sum_{r \in R_s} \mathrm{stable}(r;\, s, t, c)
$$

外部参照が変換後も整合しているか。
$\mathrm{stable}(r;\, s, t, c) \in [0, 1]$ は参照 $r$ の安定度。

#### $L_G$ — 因果構造損失

$$
L_G = \frac{\mathrm{GED}(G_s, G_t)}{\max\{1,\; |V_s| + |E_s|\}}
$$

因果・依存グラフの編集距離（Graph Edit Distance）。
正規化により $[0, 1]$ に収まる。

#### $L_A$ — 曖昧性増大損失

$$
L_A = \frac{[H(A_t \mid c) - H(A_s \mid c)]_+}{Z_A}
$$

変換により曖昧性が増大した分のみをペナルティとする。
$[\cdot]_+$ は正の部分関数（ReLU）、$Z_A$ は正規化定数。

#### $L_X$ — 極性反転損失

$$
L_X = \frac{1}{|Q_s|} \sum_{q \in Q_s}
\mathbf{1}\!\left[\mathrm{pol}(q) \neq \mathrm{pol}(\hat{q}_t)\right]
$$

制約の極性が反転した割合。

## 派生量

### 意味保存率

$$
S_{\text{sem}}(s, t \mid c) = 1 - L_{\text{sem}}(s, t \mid c)
$$

### 多段伝達

伝達系列 $x_0 \to x_1 \to \cdots \to x_n$ に対して:

$$
S_{\text{chain}} = \prod_{i=1}^{n} S_{\text{sem}}(x_{i-1}, x_i \mid c_i)
$$

$$
L_{\text{chain}} = 1 - S_{\text{chain}}
$$

各段の保存率の積。段数が増えるほど累積劣化する。

---

## 既存パイプラインとの対応

### 対応マップ

| $L_{\text{sem}}$ の項 | 現行の対応物 | f-flag | 操作化状態 |
|---|---|---|---|
| $L_P$ (命題損失) | **C 軸** — `hits / n_propositions` | — | 稼働中 (tfidf + cascade) |
| $L_Q$ (制約損失) | **f3** (演算子未処理) | f3 | 稼働中 (0.0/0.5/1.0) |
| $L_R$ (参照安定性) | **f4** (前提受容チェック) | f4 | 稼働中 (0.0/0.5/1.0, None) |
| $L_A$ (曖昧性増大) | **f1** (主題逸脱) | f1 | 稼働中 (0.0/0.5/1.0) |
| $L_G$ (因果構造) | **grv** — `compute_grv(entropy_ratio, centroid_cosine)` | — | 稼働中 (engine 統合) |
| $L_X$ (極性反転) | `detect_operator()` + miss 判定 | — | 稼働中 |
| 未割当 | **f2** (用語捏造, weight=25) | f2 | Phase 4 で検討 |

**f2 の扱い**: f2 (用語捏造) は現行パイプラインで最大重み (25) を持つが、
$L_{\text{sem}}$ の既存項に直接対応しない。偽命題の「混入」は $L_P$（命題の「欠落」）と
方向が異なる。Phase 4 の重み最適化で $L_P$ への統合または独立項化を検討する。

### 現行 $\Delta E$ の再解釈

現行の PoR 座標と $\Delta E$:

```
S = 1 - Σ(w_k × f_k) / Σ(w_k)       # f1=5, f2=25, f3=5, f4=5
C = hits / n_propositions
ΔE = (w_s(1-S)² + w_c(1-C)²) / (w_s + w_c)   # w_s=2, w_c=1
```

これは $L_{\text{sem}}$ において以下の制約を課した特殊ケースとして読める:

1. **$L_P = 1 - C$**: 命題損失を単純被覆率で近似
2. **$L_Q, L_R, L_A, L_X$ を $S$ 軸に圧縮**: f1-f4 の加重平均として混合
3. **$L_G = 0$**: 因果構造は評価対象外
4. **二乗距離で統合**: 線形和 ($L_{\text{sem}}$) ではなく加重二乗和 ($\Delta E$)

$L_{\text{sem}}$ は $S$ 軸に押し込められた異質な検出項を分離し、
各項を独立に校正・検証可能にする上位互換。

### sim 関数の操作化

| 項 | $\mathrm{sim}$ の現行実装 |
|---|---|
| $L_P$: $\mathrm{sim}_P$ | Tier 1: tfidf バイグラム Jaccard, Tier 2: SBert cosine |
| $L_Q$: $\mathrm{sim}_Q$ | ルールベース (演算子族マッチ + 極性検証) |
| $L_R$: $\mathrm{stable}$ | f4 バイナリ判定 (0.0 / 0.5 / 1.0) |
| $L_G$: $\mathrm{GED}$ | `compute_grv()` — `beta*(1-entropy_ratio) + (1-beta)*(1-centroid_cosine)` |
| $L_A$: $H(\cdot)$ | f1 バイナリ判定 (0.0 / 0.5 / 1.0) — 主題逸脱を曖昧性増大と解釈 |
| $L_X$: $\mathrm{pol}$ | `_NEGATION_POLARITY_FORMS` + 節レベルスコーピング |

---

## 設計判断

### 線形和 vs 二乗距離

$L_{\text{sem}}$ は線形和、現行 $\Delta E$ は加重二乗和。

- **線形和の利点**: 各項の寄与が加法的で解釈しやすい。「$L_P$ が 0.3 寄与した」と直読できる
- **二乗距離の利点**: 大きな欠陥に対して非線形にペナルティが増す。1 項が壊滅的でも他項で相殺されにくい
- **判断**: $L_{\text{sem}}$ は診断用（どこが壊れたか）、$\Delta E$ は判定用（verdict 閾値）として並行運用する。
  verdict 閾値 (HA48 検証済み) を無効にする必要はない

### 決定的制約との整合

現行パイプラインの設計原則は「電卓層は推論ゼロ、決定的」。

- $L_P, L_Q, L_X$: tfidf + ルールベースで決定的に計算可能
- $L_R, L_A$: f4/f3 のバイナリ値から導出可能（決定的）
- $L_G$: GED 計算は決定的だが、グラフ抽出に LLM/SBert が必要
  - cascade が既に SBert を検出層に導入済み → Tier 2 以降として位置付け可能

### 重み $\alpha, \beta, \ldots$ の決定

現行の f 重み (f1:5, f2:25, f3:5, f4:5) は経験的に決定。
$L_{\text{sem}}$ の重みも同様にアノテーションデータから校正する。

- 各項が独立 → 1 項ずつ校正可能（HA48 でも検定力が足りる）
- 初期値: 現行重みから逆算して設定
- 校正手法: Spearman $\rho$ 最大化 (human score vs $L_{\text{sem}}$)

---

## 段階的統合パス

| Phase | 内容 | 前提 |
|-------|------|------|
| 0 | 理論文書化（本ドキュメント） | なし |
| 1 | $L_P, L_Q, L_X$ を既存パイプラインの値から算出するラッパー | 既存 Evidence/State |
| 2 | $L_R$ を f4 から、$L_A$ を f1 から導出 | Phase 1 |
| 3 | $L_G$ の操作化（grv と統合、SBert or LLM ベース） | Phase 2 + cascade 基盤 |
| 4 | 重み最適化 + f2 の配置決定（HA48 + 追加アノテーションで回帰） | Phase 3 + アノテーション拡充 |

各 Phase で現行 $\Delta E$ との並行運用を維持し、
HA48 検証済み閾値を破壊しない。

---

## 応用: 多段伝達の意味劣化追跡

`experiments/orchestrator.py` の改善ループ（Claude → GPT → audit → 改善 → ...）で
各段の $S_{\text{sem}}$ を計測し、累積劣化を追跡する。

```
# 改善ループの各段で L_sem を計算
stage_losses = []
for i, (prev, curr) in enumerate(pairwise(stages)):
    L = compute_L_sem(prev, curr, context)
    stage_losses.append(L)
    if L.L_X > threshold:  # 極性反転を検出
        break  # この段で劣化が生じた

S_chain = prod(1 - L.total for L in stage_losses)
```

「何段目で止めるべきか」の判断基準を $L_{\text{sem}}$ の各項で与える。

---

## 参考: grv への接続

CLAUDE.md で「grv 操作化は未着手（中期タスク）」とされている。
$L_G$（因果・依存グラフの構造距離）は grv の操作的定義の候補:

- **grv**: 回答内の語彙重力分布 — 「重い」語彙が論理構造の核にあるか
- **$L_G$**: 因果グラフの編集距離 — 構造がどれだけ変形したか
- **接続**: grv を「$G_x$ のノード重みの分布」として定義すれば、
  $L_G$ は重み付き GED として grv を内包できる

---

## ステータス

- Phase 0: **完了** (本ドキュメント)
- Phase 1: **完了** (`semantic_loss.py` — L_P, L_Q, L_X)
- Phase 2: **完了** (L_R = f4, L_A = f1)
- Phase 3: **完了** (L_G = grv, engine の `compute_grv()` 統合)
- Phase 4: 未着手 (重み最適化 + f2 の配置決定)
