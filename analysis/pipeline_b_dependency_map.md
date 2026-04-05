# パイプライン B 依存関係マップ

> 作成日: 2026-04-05
> 目的: パイプライン B の廃止に先立ち、全依存関係を把握する（コード変更なし）

---

## 1. 削除候補ファイル/関数の一覧

### 1-1. ugh_audit/scorer/ugh_scorer.py（UGHScorer クラス）

| 要素 | 行番号 | 説明 |
|------|--------|------|
| `UGHScorer` クラス定義 | L76 | cosine PoR / cosine ΔE / token grv のスコアラー |
| `score()` | L105–135 | メインエントリポイント（3層フォールバック） |
| `_score_with_ugh3()` | L155–229 | Layer 1: ugh3-metrics-lib (PorV4, DeltaE4, GrvV4) |
| `_score_with_st()` | L235–288 | Layer 2: sentence-transformers (cosine PoR/ΔE) |
| `_score_minimal()` | L448–468 | Layer 3: minimal stub（ゼロ値返却） |
| `backend` property | L138–144 | 使用中バックエンド名 |
| `last_backend` property | L147–149 | 最後に使用したバックエンド名 |
| `_compute_grv()` | L294–305 | grv 計算ディスパッチ |
| `_grv_with_fugashi()` | L307–389 | fugashi 形態素解析による token 頻度 grv |
| `_grv_with_regex()` | L391–411 | regex フォールバック grv |
| `_extract_head_sentences()` | L418–446 | delta_e_summary 用テキスト切り出し |
| `POR_FIRE_THRESHOLD` 定数 | L27 | 0.82（cosine PoR 発火閾値） |

#### cosine PoR 計算箇所

`_score_with_st()` 内 L247–250:
```python
q_emb = model.encode(question, normalize_embeddings=True)
r_emb = model.encode(response, normalize_embeddings=True)
por = float(np.dot(q_emb, r_emb))  # cosine similarity
por_fired = por >= POR_FIRE_THRESHOLD
```

#### cosine ΔE 計算箇所

`_score_with_st()` 内 L252–268:
```python
delta_e_full = float(1.0 - np.dot(ref_emb, r_emb))
delta_e_core = float(1.0 - np.dot(ref_core_emb, r_emb))
delta_e_summary = float(1.0 - np.dot(ref_core_emb, r_head_emb))
```

3 種の cosine ΔE バリアント: `delta_e_full`, `delta_e_core`, `delta_e_summary`

#### grv トークン頻度計算箇所

- `_compute_grv()` (L294–305) → `_grv_with_fugashi()` (L307–389) / `_grv_with_regex()` (L391–411)
- 出力: `Dict[str, float]` — 上位 10 トークンの正規化頻度

### 1-2. ugh_audit/scorer/models.py（AuditResult）

| 要素 | 行番号 | 説明 |
|------|--------|------|
| `AuditResult` dataclass | L11–73 | パイプライン B 専用の結果モデル |
| `por` フィールド | L23 | cosine PoR 値 |
| `por_fired` フィールド | L24 | cosine PoR 発火フラグ |
| `delta_e` フィールド | L25 | cosine ΔE (= delta_e_full) |
| `delta_e_core` フィールド | L26 | cosine ΔE (core) |
| `delta_e_full` フィールド | L27 | cosine ΔE (full) |
| `delta_e_summary` フィールド | L28 | cosine ΔE (summary) |
| `grv` フィールド | L29 | トークン頻度辞書 |
| `meaning_drift` property | L48–55 | delta_e 閾値分類 |
| `dominant_gravity` property | L58–65 | grv の最大トークン |

### 1-3. ugh_audit/scorer/__init__.py

| 要素 | 行番号 | 説明 |
|------|--------|------|
| `AuditResult` re-export | L1 | `from ugh_audit.scorer.models import AuditResult` |
| `UGHScorer` re-export | L2 | `from ugh_audit.scorer.ugh_scorer import UGHScorer` |

### 1-4. detector.py — compute_quality_score()

| 要素 | 行番号 | 説明 |
|------|--------|------|
| `compute_quality_score()` | L1213–1244 | Model C' ボトルネック型品質スコア |
| `QUALITY_ALPHA` | L35 | 0.4 |
| `QUALITY_BETA` | L36 | 0.0 |
| `QUALITY_GAMMA` | L37 | 0.8 |
| `QUALITY_MODEL_NAME` | L38 | "bottleneck_v1" |

> **注**: `compute_quality_score()` は `delta_e_full` を入力に取るが、パイプライン A の `ΔE` とパイプライン B の `cosine ΔE` のどちらでも使用可能な汎用関数。B 専用ではないが、B の cosine ΔE 前提でパラメータ校正されている (n=20 LOO-CV)。

---

## 2. 依存元の一覧（呼び出し元）

### 2-1. UGHScorer の依存元

| 呼び出し元ファイル | 行番号 | 用途 |
|------------------|--------|------|
| `ugh_audit/__init__.py` | L10 | 公開 API export |
| `ugh_audit/scorer/__init__.py` | L2 | モジュール export |
| `ugh_audit/server.py` | L26, L128–137 | REST API: import + `_get_scorer()` で遅延初期化 |
| `ugh_audit/mcp_server.py` | L21, L45–54 | MCP サーバー: import + `_get_scorer()` で遅延初期化 |
| `ugh_audit/collector/audit_collector.py` | L27, L48 | AuditCollector の `__init__` でインスタンス生成 |
| `examples/basic_audit.py` | L5, L9 | E2E サンプル |
| `scripts/rescore_phase_c.py` | L24, L64 | リスコアリングスクリプト |
| `scripts/score_phase_c.py` | L28, L30 | スコアリングスクリプト |
| `tests/test_scorer.py` | L6, L33 | UGHScorer 単体テスト |
| `tests/test_scorer_st.py` | L12, L28 | sentence-transformers バックエンドテスト |
| `tests/test_grv_ja.py` | L10, L26, L43, L55, L76 | grv 日本語テスト |
| `tests/test_delta_e_variants.py` | L5 | ΔE バリアントテスト |
| `tests/test_server.py` | L8 | REST API テスト |
| `tests/test_mcp_server.py` | L12 | MCP テスト |
| `tests/test_imports.py` | L9, L26 | import テスト |
| `README.md` | L161 | ドキュメント |

### 2-2. AuditResult の依存元

| 呼び出し元ファイル | 行番号 | 用途 |
|------------------|--------|------|
| `ugh_audit/__init__.py` | L9 | 公開 API export |
| `ugh_audit/scorer/__init__.py` | L1 | モジュール export |
| `ugh_audit/scorer/ugh_scorer.py` | L17 | `score()` の戻り値型 |
| `ugh_audit/storage/audit_db.py` | L12, L85 | `save(result: AuditResult)` で永続化 |
| `ugh_audit/collector/audit_collector.py` | L26 | 戻り値型 |
| `tests/test_scorer.py` | L5 | テスト |
| `tests/test_scorer_st.py` | L38 | テスト |
| `tests/test_delta_e_variants.py` | L6 | テスト |
| `tests/test_por_boundary.py` | L5 | テスト |
| `tests/test_collector.py` | L7 | テスト |
| `tests/test_storage.py` | L6 | テスト |
| `tests/test_imports.py` | L8, L26 | テスト |

### 2-3. POR_FIRE_THRESHOLD の依存元

| 呼び出し元ファイル | 行番号 | 用途 |
|------------------|--------|------|
| `tests/test_scorer_st.py` | L39 | テストで閾値参照 |
| `tests/test_por_boundary.py` | L6 | テストで閾値参照 |

### 2-4. compute_quality_score() の依存元

| 呼び出し元ファイル | 行番号 | 用途 |
|------------------|--------|------|
| `tests/test_quality_score.py` | L19, L35, L50, L60, L72, L100 | 5テストケースで直接呼び出し |

> **注**: `compute_quality_score()` は REST API / MCP サーバーからは直接呼ばれていない。テストからのみ呼ばれている独立関数。

### 2-5. AuditDB.save() — AuditResult 依存の永続化

| カラム | AuditResult フィールド | パイプライン B 固有 |
|--------|----------------------|-------------------|
| `por` | `result.por` | **Yes** (cosine PoR) |
| `por_fired` | `result.por_fired` | **Yes** |
| `delta_e` | `result.delta_e` | **Yes** (cosine ΔE) |
| `delta_e_core` | `result.delta_e_core` | **Yes** |
| `delta_e_full` | `result.delta_e_full` | **Yes** |
| `delta_e_summary` | `result.delta_e_summary` | **Yes** |
| `grv_json` | `json.dumps(result.grv)` | **Yes** (token 頻度) |
| `meaning_drift` | `result.meaning_drift` | **Yes** |

---

## 3. B→A 対応表

| パイプライン B（削除候補） | パイプライン A（ugh_calculator.py） | 対応状況 |
|--------------------------|----------------------------------|---------|
| `UGHScorer.score()` → `AuditResult` | `calculate(evidence)` → `State` | **要差し替え**: 入出力モデルが異なる |
| cosine PoR (`np.dot(q_emb, r_emb)`) | `por_state = "inactive"` | **A に対応なし** ⚠️ A では PoR 非使用 |
| cosine ΔE (`1 - cosine_sim`) | `_compute_delta_e(s, c)` (加重二乗和) | **計算方式が異なる**: B=cosine距離, A=S/C加重二乗和 |
| `delta_e_core`, `delta_e_summary` | なし | **A に対応なし** ⚠️ A は ΔE 1種のみ |
| grv トークン頻度 (`Dict[str, float]`) | `_grv_tag()` → `"none"` (未実装) | **A に対応なし** ⚠️ A では grv 未操作化 |
| `AuditResult` (frozen dataclass) | `State` (frozen dataclass) | **要差し替え**: フィールド構成が異なる |
| `AuditResult.meaning_drift` | `State.delta_e_bin` | **部分対応**: B は文字列分類, A は整数ビン |
| `AuditResult.dominant_gravity` | なし | **A に対応なし** ⚠️ |
| `POR_FIRE_THRESHOLD` | なし | **A に対応なし** ⚠️ A では PoR 非使用 |
| `compute_quality_score()` | なし（detector.py 内の独立関数） | **検討要** ⚠️ パイプライン B 専用ではないが cosine ΔE 前提で校正 |

### パイプライン A の関数一覧（ugh_calculator.py）

| 関数 | 行番号 | 説明 |
|------|--------|------|
| `_clamp(value, lo, hi)` | L62–64 | 値クランプ |
| `_compute_s(evidence)` | L67–80 | S = 1 - Σ(w_k × f_k) / Σ(w_k) |
| `_compute_c(evidence)` | L83–91 | C = hits / n_propositions |
| `_compute_delta_e(s, c)` | L94–102 | ΔE = (w_s(1-S)² + w_c(1-C)²) / (w_s + w_c) |
| `_bin_delta_e(delta_e)` | L105–119 | ΔE → ビン (1–4) |
| `_bin_c(c)` | L122–133 | C → ビン (1–3) |
| `_grv_tag(evidence)` | L136–141 | grv タグ (常に "none") |
| `calculate(evidence)` | L144–163 | Evidence → State メイン関数 |

### パイプライン A のデータモデル（ugh_calculator.py）

| データクラス | 行番号 | 説明 |
|------------|--------|------|
| `Evidence` | L29–46 | 検出層出力 (f1–f4, propositions_hit/total, hit_sources) |
| `State` | L49–59 | 電卓層出力 (S, C, delta_e, delta_e_bin, C_bin, por_state, grv_tag) |

---

## 4. 未対応の依存（A に対応関数がないもの） ⚠️

| 項目 | 説明 | 対応方針の候補 |
|------|------|--------------|
| **cosine PoR** | A では PoR 非使用 (`por_state = "inactive"`)。PoR の廃止 or 別方式の検討が必要 | 廃止（ρ≒0 で無効） |
| **cosine ΔE 3 バリアント** | A は S/C ベースの ΔE 1 種のみ。`delta_e_core`, `delta_e_summary` に対応なし | A の ΔE に統一（3バリアント廃止） |
| **grv トークン頻度** | A は `_grv_tag()` が `"none"` 固定。token 辞書なし | 将来実装 or 廃止（grv 操作化は未着手） |
| **AuditResult** | A は `State` を使用。フィールド構成が異なる | `State` に統一 or 新データモデル設計 |
| **AuditDB.save()** | `AuditResult` のフィールドを前提とした INSERT 文 | `State` 対応に改修が必要 |
| **server.py / mcp_server.py** | `UGHScorer` を直接使用。A のパイプラインに接続されていない | detector.py → ugh_calculator.py パイプラインへの差し替え |
| **AuditCollector** | `UGHScorer` を内包。A のパイプラインに接続されていない | 同上 |
| **compute_quality_score()** | cosine ΔE で校正済み。A の ΔE で再校正が必要 | n=48 で再校正予定 |

---

## 5. 影響範囲サマリ

### 削除対象ファイル

| ファイル | 理由 |
|---------|------|
| `ugh_audit/scorer/ugh_scorer.py` | パイプライン B 本体 |
| `ugh_audit/scorer/models.py` | B 専用データモデル |
| `ugh_audit/scorer/__init__.py` | B の export（書き換え or 削除） |

### 改修対象ファイル

| ファイル | 改修内容 |
|---------|---------|
| `ugh_audit/__init__.py` | `UGHScorer`, `AuditResult` の export 削除 or 差し替え |
| `ugh_audit/server.py` | `UGHScorer` → A パイプライン接続に差し替え |
| `ugh_audit/mcp_server.py` | 同上 |
| `ugh_audit/collector/audit_collector.py` | `UGHScorer` → A パイプライン接続に差し替え |
| `ugh_audit/storage/audit_db.py` | `AuditResult` → `State` 対応に改修 |
| `examples/basic_audit.py` | A パイプライン使用に書き換え |
| `scripts/rescore_phase_c.py` | 同上 |
| `scripts/score_phase_c.py` | 同上 |
| `README.md` | API 例の更新 |

### 改修対象テスト

| テストファイル | 改修内容 |
|--------------|---------|
| `tests/test_scorer.py` | B テスト → A テストに書き換え or 削除 |
| `tests/test_scorer_st.py` | 削除（sentence-transformers バックエンド固有） |
| `tests/test_grv_ja.py` | 削除（token 頻度 grv 固有） |
| `tests/test_delta_e_variants.py` | 削除（cosine ΔE 3 バリアント固有） |
| `tests/test_por_boundary.py` | 削除（cosine PoR 固有） |
| `tests/test_server.py` | `UGHScorer` モック → A パイプラインに差し替え |
| `tests/test_mcp_server.py` | 同上 |
| `tests/test_collector.py` | `AuditResult` → `State` に差し替え |
| `tests/test_storage.py` | `AuditResult` → `State` に差し替え |
| `tests/test_imports.py` | export 名の更新 |
| `tests/test_quality_score.py` | `compute_quality_score()` の扱い次第（A の ΔE で再校正後に更新） |
