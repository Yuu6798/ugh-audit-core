# grv v1.4 — 因果構造損失 (語彙重力指標)

回答が問いの重力圏からどれだけ逸脱しているかを測る偏差スコア。
`0` = 構造安定、`1` = 構造偏差。

## 合成式

```
grv = normalize(w_d * drift + w_s * dispersion + w_c * collapse_v2)
```

| 成分 | 確定重み | 備考 |
|------|---------|------|
| drift | 0.70 | 主成分。生コサインベース |
| dispersion | 0.05 | 内部指標。cos01 維持 |
| collapse_v2 | 0.25 | residual 型。V-4 増分寄与プラス確認済み |

HA48 検証: ρ(grv, human_score) = **-0.357**, σ = 0.051

## 成分

### drift (重心逸脱)

```
raw_cos = cosine_similarity(G_res, G_ref)  # [-1, 1]
drift = 1 - max(0, raw_cos)
```

v1.3 で cos01 から生コサインに変更。圧縮問題を回避。

### dispersion (内部散漫度)

```
dispersion = mean(1 - cos01(s_i, G_res))  (n_sent > 1)
dispersion = 0.0                          (n_sent <= 1)
```

cos01 を維持（参照非依存の内部指標のため圧縮が問題にならない）。

### collapse_v2 (残留型偏在集中度)

```
aff_i = [max(cos01(u_i, p_k) for p_k in propositions) for u_i in units]
collapse_v2 = mean(1 - a for a in aff_i)
```

v1.4 で entropy 型から residual 型に再設計。
「命題で説明できない残留が多いほど高い」。

旧 entropy 型 (v1.2-v1.3) は「均等分布 = 良い」の暗黙の前提が偽で、
HA48 で方向逆転（増分寄与マイナス）が確認された。

## 補助計測

### cover_soft

```
cover_soft = mean(max(cos01(p_k, u_i) for u_i in units) for p_k in propositions)
```

命題→応答の連続到達度。C (二値判定) の補助計測として並走。
ρ(cover_soft, human_score) = 0.314。

### wash_index

```
wash_index = collapse_v2 × cover_soft
wash_index_c = collapse_v2 × C_normalized
```

「一見届いているのに中身がない」状態の検出:
- safety-washing: collapse_v2 高 × cover_soft 高 → wash_index 高
- 良い集中回答: collapse_v2 低 × cover_soft 高 → wash_index 低

## 参照重心

```
meta_scale = max(0, 2 × ref_confidence - 1)
G_ref = normalize(w_q × G_q + meta_scale × w_m × G_m)
```

| 条件 | w_q | w_m | ref_confidence | meta_scale |
|------|----:|----:|--------------:|-----------:|
| manual meta | 0.60 | 0.40 | 1.00 | 1.00 |
| auto meta | 0.80 | 0.20 | 0.70 | 0.40 |
| missing | 1.00 | 0.00 | 0.50 | 0.00 |

## フォールバック

| 条件 | 処理 |
|------|------|
| n_sent == 1 | dispersion = 0.0 |
| n_props == 0 | collapse_v2 = 0.0, cover_soft = 0.0 |
| meta 欠落 | G_ref = G_q のみ |
| 文分割失敗 | 全文を1文として扱う |
| SBert 未導入 | grv = None (L_sem で L_G 除外) |

## 出力スキーマ

```json
{
  "grv": 0.35,
  "grv_tag_provisional": "mid_gravity",
  "grv_components": {
    "drift": 0.28,
    "dispersion": 0.15,
    "collapse_v2": 0.42
  },
  "cover_soft": 0.72,
  "wash_index": 0.30,
  "wash_index_c": 0.28,
  "grv_meta": {
    "n_sentences": 6,
    "n_propositions": 3,
    "collapse_v2_applicable": true,
    "meta_source": "manual",
    "ref_confidence": 1.0,
    "embedding_backend": "paraphrase-multilingual-MiniLM-L12-v2",
    "grv_version": "v1.4",
    "weights": {"w_d": 0.70, "w_s": 0.05, "w_c": 0.25}
  },
  "grv_debug": {
    "prop_affinity_per_sentence": [],
    "cover_soft_per_proposition": [],
    "drift_raw_cosine": 0.0
  }
}
```

## 暫定タグ

| grv | タグ |
|-----|------|
| >= 0.66 | high_gravity |
| >= 0.33 | mid_gravity |
| < 0.33 | low_gravity |

## L_sem との接続

`semantic_loss.py` の `L_G = clamp(grv)` (δ=0.13) として統合。
grv=None 時は L_G が除外され、残り6項で正規化。

## 埋め込みバックエンド

| 項目 | 値 |
|------|-----|
| モデル | `paraphrase-multilingual-MiniLM-L12-v2` |
| 共有方式 | `cascade_matcher.get_shared_model()` シングルトン |

## バージョン履歴

| ver | 変更 | ρ |
|-----|------|---|
| v1.2 | 3項式 (entropy 型 collapse) | N/A (σ不足) |
| v1.3 | 2成分確定 (collapse 除外) | -0.318 |
| v1.4 | collapse_v2 (residual型) + cover_soft + wash_index | **-0.357** |

## 次ステップ: 判定層ロードマップ

Phase B (mode_affordance v1) 実装済み。response_mode_signal として非破壊信号を提供。
詳細: [`mode_affordance.md`](mode_affordance.md), [`addendum`](mode_affordance_v1_addendum.md)
Phase C〜E (mode_conditioned_grv + 判定層合成) は v1 の 48件以上較正後に着手予定。

## 関連ファイル

- `grv_calculator.py` — 3成分計算エンジン
- `ugh_calculator.py` — State.grv フィールド
- `semantic_loss.py` — L_G 統合
- `cascade_matcher.py` — SBert シングルトン + 埋め込みキャッシュ
- `ugh_audit/server.py` / `mcp_server.py` — API レスポンスの grv フィールド
- `analysis/grv_v14_acceptance.py` — HA48 受け入れ試験
