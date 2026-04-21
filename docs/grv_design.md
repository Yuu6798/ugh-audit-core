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

## タグ閾値 (HA48 校正済み)

| grv | タグ | 旧値 (暫定) |
|-----|------|------------|
| >= 0.30 | high_gravity | >= 0.66 |
| >= 0.20 | mid_gravity | >= 0.33 |
| < 0.20 | low_gravity | < 0.33 |

HA48 分布: mean=0.185, σ=0.051, range=[0.10, 0.31]。
旧暫定値では全48件が low_gravity に分類され、タグ分類が機能していなかった。

## L_sem との接続

`semantic_loss.py` の `L_G = clamp(grv)` として統合。`DEFAULT_WEIGHTS`
での重みは `L_G=0.35` (LOO-CV 補正後の runtime 値、
`semantic_loss.py:34-47` 参照)。full-sample 最適では `L_G=0.850` が
選ばれたが、n=48 で shrinkage=0.128 を確認して過学習抑制のため
0.35 に補正した。grv=None 時は L_G が除外され、残り 6 項で正規化。

## 埋め込みバックエンド

| 項目 | 値 |
|------|-----|
| モデル | `paraphrase-multilingual-MiniLM-L12-v2` |
| 共有方式 | `cascade_matcher.get_shared_model()` シングルトン |

## トークナイズ・形態素解析パイプライン

grv の入力は「質問 Q / 回答 R / core_propositions」の 3 種テキスト。これを
以下の 2 段階で数値化する:

### 1. 文分割 (`_split_sentences` @ `grv_calculator.py:52-55`)

```python
re.split(r'[。．！？!?.\n]+', text)
```

- 日本語句読点 (`。．！？`) と Western 終端 (`!?.` + 改行) を等価に扱う
- **形態素解析器には依存しない**。正規表現のみ
- 分割結果が空になったら全文を 1 文扱いで継続 (`n_sent=1` フォールバックで
  `dispersion=0.0`)

### 2. 埋め込み (`cascade_matcher.encode_texts`)

- SBert モデル `paraphrase-multilingual-MiniLM-L12-v2` に委譲
- このモデルは XLM-RoBERTa ベースの SentencePiece tokenizer を内蔵
  (約 50 言語カバー、日本語・英語・中国語・韓国語などを語彙レベルで処理)
- grv パス自体には `fugashi` / `ipadic` / `MeCab` 等の**明示的な形態素
  解析器を使っていない**。トークナイズは SBert に閉じている

### pyproject の extras について

`fugashi` / `ipadic` は過去に `[ja]` / `[full]` / `[server]` extras で
宣言されていたが、repo 内 Python コードから import されたことが
一度もなかった (`grep -r "fugashi\|ipadic\|MeCab" --include="*.py"` で
ヒット 0)。Phase 7 v0 / v1 の calibration note では「将来の形態素 POS
filter 導入で不正トークンを完全除去する」計画が言及されていたが、
実装されないまま 2026-04 に cleanup で extras から削除した。

形態素解析を後日導入する場合は、現在と同様に `fugashi>=1.3` /
`ipadic>=1.0` を追加する extra を新設する (例: `[morpheme]`)。
SBert パス自体は `paraphrase-multilingual-MiniLM-L12-v2` の
SentencePiece で完結するため不要。

## 多言語対応方針

### カバー範囲

| 言語 / 系 | 文分割 | 埋め込み | 備考 |
|----------|--------|---------|------|
| 日本語 | ✓ (`。．！？`) | ✓ (XLM-R) | 主検証言語 |
| 英語 | ✓ (`!?.` + `\n`) | ✓ (XLM-R) | HA20 で副次検証 |
| 中国語 / 韓国語 | ✓ (`。！？` 共通) | ✓ (XLM-R) | 未検証、理論上は動く |
| タイ語 / アラビア語 | △ (句読点が異なる) | ✓ (XLM-R) | 文分割で全文 1 文扱いになる可能性 |
| 改行なし長文 (任意言語) | △ (1 文扱い) | ✓ | `dispersion=0.0` 固定になる |

### 既知の制限

1. **文分割が句読点ルールベース**: タイ語 (句読点を基本使わない)
   アラビア語 (`؟` `،` など独自記号) では分割が不十分になりうる。影響は
   `dispersion` / `collapse_v2` の粒度低下 (全文 1 文扱いで成分劣化)
2. **形態素単位の一致判定なし**: detector の bigram Jaccard は
   **文字 bigram** ベース。CJK では機能するが、空白区切り言語では
   意味的に近い語が別 bigram になる
3. **言語検出なし**: 入力言語に応じて分割ルールを切り替える仕組みは
   持たない。常に日英ハイブリッドの正規表現で処理する
4. **検証カバレッジ**: HA48 / HA20 / HA-accept40 は全件日本語
   (`data/human_annotation_*/`)。他言語での経験的 ρ は未取得

### 将来の改善余地

| 方向 | 効果 | 工数 |
|------|------|------|
| `pysbd` 等の言語対応 sentence splitter 導入 | タイ語・アラビア語で `dispersion` / `collapse_v2` が本来の粒度で動作 | 低 (入れ替えのみ) |
| fugashi ベースの形態素 bigram を detector に実装 | 日本語の命題マッチ精度向上 (導入時は新規 `[morpheme]` extra を切る) | 中 (detector 側の hit 判定改修) |
| 多言語での ρ 検証 (英・中・韓で各 n=20+) | 論文での汎用性主張の根拠になる | 高 (アノテーション作業) |
| XLM-R 以外のモデル差し替え (LaBSE 等) の比較 | 特定言語ペアで精度向上の可能性 | 中 (校正再走) |

## バージョン履歴

| ver | 変更 | ρ |
|-----|------|---|
| v1.2 | 3項式 (entropy 型 collapse) | N/A (σ不足) |
| v1.3 | 2成分確定 (collapse 除外) | -0.318 |
| v1.4 | collapse_v2 (residual型) + cover_soft + wash_index | **-0.357** |

## mode_conditioned_grv v2 (Phase 7) — 実装済み

grv_raw と mode_affordance を組み合わせ、モード固有の 4 成分解釈ベクトルを生成する。
grv_raw を置き換えない。説明用ベクトルとして併走。

### 4 成分

| 成分 | 意味 | 方向 | HA48 ρ(vs O) |
|------|------|------|-------------|
| anchor_alignment | 問いの核 + 命題への到達度 | 高=良 | **+0.4063** (p=0.004) |
| balance | 命題カバレッジの均等性 | 高=良 | n=5 (データ不足) |
| boilerplate_risk | ボイラープレート密度 | 高=危 | 信号なし (HA48 低密度) |
| collapse_risk | 論点の 1 塊集中度 | 高=危 | **-0.3191** (p=0.027) |

### モード別の重要成分

| mode | focus_components |
|------|-----------------|
| definitional | anchor_alignment |
| analytical | anchor_alignment |
| evaluative | anchor_alignment, boilerplate_risk |
| comparative | balance, anchor_alignment |
| critical | anchor_alignment, boilerplate_risk |
| exploratory | collapse_risk, balance |

実装: `mode_grv.py`
検証: `analysis/mode_grv_ha48_check.py`

## 次ステップ: 判定層ロードマップ

Phase 6 (mode_affordance v1) 実装済み。response_mode_signal として非破壊信号を提供。
Phase 7 (mode_conditioned_grv v2) 実装済み。4成分解釈ベクトルを併走出力。
Phase 8 (verdict_advisory, mcg → downgrade) は anchor_alignment の HA48 ρ=+0.41 を踏まえて設計・ship 済み (n=63 校正)。旧 Phase D (support_signal 独立) は廃止、目的は Phase 8 で吸収。
詳細: [`mode_affordance.md`](mode_affordance.md), [`addendum`](mode_affordance_v1_addendum.md)

## 関連ファイル

- `grv_calculator.py` — 3成分計算エンジン
- `mode_grv.py` — mode_conditioned_grv v2 (4成分解釈ベクトル)
- `ugh_calculator.py` — State.grv フィールド
- `semantic_loss.py` — L_G 統合
- `cascade_matcher.py` — SBert シングルトン + 埋め込みキャッシュ
- `ugh_audit/server.py` / `mcp_server.py` — API レスポンスの grv + mode_conditioned_grv
- `analysis/grv_v14_acceptance.py` — HA48 受け入れ試験
- `analysis/calibrate_grv_lsem.py` — grv/L_sem Phase 5 統合校正
- `analysis/mode_grv_ha48_check.py` — mode_conditioned_grv HA48 検証
