# grv v1.2 — 因果構造損失 (語彙重力指標)

回答が問いの重力圏からどれだけ逸脱しているかを測る偏差スコア。
`0` = 構造安定、`1` = 構造偏差。

## 3成分

### drift (重心逸脱)

```
drift = 1 - cos01(G_res, G_ref)
```

- `G_res`: 応答文ベクトル群の平均
- `G_ref`: 参照重心 (後述)
- 解釈: 0 = 問いの重力圏内、1 = 逸脱

### dispersion (内部散漫度)

```
dispersion = mean(1 - cos01(s_i, G_res))  (n_sent > 1)
dispersion = 0.0                          (n_sent <= 1)
```

- `s_i`: 応答の各文ベクトル
- 解釈: 0 = 内部がまとまっている、1 = 散漫

### collapse (偏在集中度)

```
a_k = prop_weight_k × max(cos01(s_i, p_k))
p_dist = normalize(a_k)
collapse = 1 - H(p_dist) / log(K)        (n_props >= 2)
collapse = 0.0                            (n_props < 2)
```

- `p_k`: 参照命題群のベクトル
- auto-meta 時は `collapse *= meta_scale` で減衰
- 解釈: 複数命題へ均等に届けば低い。一点集中で高い

## 合成式

```
grv = clamp(w_d × drift + w_s × dispersion + w_c × collapse, 0, 1)
```

| 成分 | 暫定重み | 備考 |
|------|---------|------|
| drift | 0.50 | 主成分。HA48 proxy で global correlation 最強 |
| dispersion | 0.20 | 補助項。単独弁別力が弱い |
| collapse | 0.30 | 補助成分。専用失敗モード検出器 |

**この重みは Phase 2 (HA48 SBert 実値検証) で再校正するまで確定値ではない。**

collapse 非適用時 (n_props < 2) は drift + dispersion のみで重み再配分。

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
| n_props < 2 | collapse = 0.0, collapse_applicable = False |
| meta 欠落 | G_ref = G_q のみ, ref_confidence = 0.50 |
| 文分割失敗 | 全文を1文として扱う |
| SBert 未導入 | grv = None (L_sem で L_G 除外) |

## 出力スキーマ

```json
{
  "grv": 0.41,
  "grv_components": {
    "drift": 0.32,
    "dispersion": 0.18,
    "collapse": 0.57
  },
  "grv_meta": {
    "n_sentences": 6,
    "n_propositions": 3,
    "collapse_applicable": true,
    "meta_source": "manual",
    "ref_confidence": 1.0,
    "meta_scale": 1.0,
    "prop_weights": [1.0, 1.0, 0.4]
  },
  "grv_debug": {
    "prop_affinity": [0.72, 0.45, 0.31],
    "grv_tag_provisional": "mid_gravity"
  }
}
```

## 暫定タグ

| grv | タグ |
|-----|------|
| >= 0.66 | high_gravity |
| >= 0.33 | mid_gravity |
| < 0.33 | low_gravity |

Phase 2 で HA48 分布に基づき再校正予定。

## L_sem との接続

`semantic_loss.py` の `L_G = clamp(grv)` (δ=0.13) として統合。
grv=None 時は L_G が除外され、残り6項で正規化。

## 埋め込みバックエンド

| 項目 | 値 |
|------|-----|
| モデル | `paraphrase-multilingual-MiniLM-L12-v2` |
| 共有方式 | `cascade_matcher.get_shared_model()` シングルトン |
| キャッシュ | 応答文は非キャッシュ、命題は `encode_texts_cached()` 経由 |

## 陽性例 (collapse 検出対象)

1. **安全語彙への集中** (safety-washing)
2. **単一論点への潰れ**
3. **一般論 attractor への退避**
4. **前提受容による一本化**

HA48 準陽性: q067, q099, q093, q086, q037, q074

## Phase 2 検証基準 (本実装スコープ外)

- 非退化性: HA48 全件で grv の標準偏差 > 0.05
- 準陽性群で collapse が負例群より高い
- drift+dispersion のみより +collapse で ρ 改善
- ΔE_A と独立の説明力

## 関連ファイル

- `grv_calculator.py` — 3成分計算エンジン
- `ugh_calculator.py` — State.grv フィールド
- `semantic_loss.py` — L_G 統合
- `cascade_matcher.py` — SBert シングルトン + 埋め込みキャッシュ
- `ugh_audit/server.py` / `mcp_server.py` — API レスポンスの grv フィールド
