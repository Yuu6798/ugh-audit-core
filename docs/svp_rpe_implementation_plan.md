# SVP-RPE 統合実装プラン

## Context

音楽ファイル（WAV/MP3）から RPE を抽出し、決定論的に SVP を生成し、
UGHer 系 + RPE 系の二系統評価を行うローカル完結型ツールを構築する。

**ゴール**: 「分析 → 設計 → 生成準備 → 評価」の循環をコードとして再現する。
API キー不要、LLM 不要、同一入力 → 同一出力の完全決定論的パイプライン。

---

## 開発方針

**新規リポジトリをローカルに立ち上げて開発する。**
ugh-audit-core とは独立したプロジェクトとして構築する。
ugh-audit-core の設計パターン（PoR/ΔE/grv、三層パイプライン、
値クランプ、Optional 伝播）は参照するが、コード依存は持たない。

**ブランチ**: ugh-audit-core の `claude/general-conversation-EdfOY` には
設計ドキュメントのリンクや参照のみ。本体コードは新規リポジトリに配置。

---

## ディレクトリ構成

```
svp-rpe/                           # 新規リポジトリルート
├── README.md
├── pyproject.toml
├── .gitignore
├── config/
│   ├── pro_baseline.yaml          # RPE Pro 基準値
│   ├── semantic_rules.yaml        # semantic 生成ルール
│   └── svp_templates.yaml         # SVP テンプレート
├── docs/
│   ├── architecture.md            # アーキテクチャ設計書
│   ├── metrics.md                 # RPE 指標定義
│   └── cli.md                     # CLI リファレンス
├── src/
│   └── svp_rpe/
│       ├── __init__.py
│       ├── cli.py                 # typer CLI (svprpe コマンド)
│       ├── io/
│       │   ├── __init__.py
│       │   └── audio_loader.py    # WAV/MP3 読み込み + AudioMetadata
│       ├── rpe/
│       │   ├── __init__.py
│       │   ├── models.py          # PhysicalRPE, SemanticRPE, RPEBundle
│       │   ├── extractor.py       # RPE 統合パイプライン
│       │   ├── physical_features.py  # librosa ベース物理特徴量計算
│       │   ├── semantic_rules.py  # ルールベース意味層生成
│       │   └── structure.py       # RMS/novelty ベースセグメント分割
│       ├── svp/
│       │   ├── __init__.py
│       │   ├── models.py          # SVPBundle
│       │   ├── generator.py       # RPE → SVP 決定論的変換
│       │   ├── templates.py       # テンプレート定義
│       │   ├── render_yaml.py     # YAML 出力
│       │   └── render_text.py     # Markdown/TXT 出力
│       ├── eval/
│       │   ├── __init__.py
│       │   ├── models.py          # UGHerScore, RPEScore, IntegratedScore
│       │   ├── scorer_rpe.py      # RPE 系評価
│       │   ├── scorer_ugher.py    # UGHer 系評価
│       │   └── scorer_integrated.py  # 統合スコア
│       └── utils/
│           ├── __init__.py
│           └── config_loader.py   # YAML config 読み込み
├── tests/
│   ├── conftest.py                # 共通 fixture（合成 WAV 生成等）
│   ├── test_audio_loader.py
│   ├── test_rpe_extractor.py
│   ├── test_svp_generator.py
│   ├── test_eval_rpe.py
│   ├── test_eval_ugher.py
│   └── test_cli.py
└── examples/
    ├── sample_input/
    └── expected_output/
```

---

## pyproject.toml

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "svp-rpe"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "numpy>=1.24",
    "scipy>=1.10",
    "librosa>=0.10",
    "soundfile>=0.12",
    "pydantic>=2.0",
    "typer>=0.9",
    "pyyaml>=6.0",
    "rich>=13.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "ruff>=0.1"]

[project.scripts]
svprpe = "svp_rpe.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.ruff]
line-length = 100
target-version = "py311"
```

---

## 実装順序（P0、7 ステップ）

### Step 1: 骨格作成
- リポジトリ初期化 (`git init`)
- ディレクトリ構成 + `__init__.py` + pyproject.toml + .gitignore
- 全 Pydantic モデル定義
  - AudioMetadata, SpectralProfile, StereoProfile, SectionMarker
  - PhysicalRPE, SemanticRPE, RPEBundle
  - SVP 関連（DataLineage, AnalysisRPE, SVPForGeneration, EvaluationCriteria, MinimalSVP, SVPBundle）
  - 評価（UGHerScore, RPEScore, IntegratedScore）
- schema_version + Optional confidence パターン統一

### Step 2: audio ingestion (`io/audio_loader.py`)
- `load_audio(path) -> AudioData`（librosa.load ラッパー）
- `to_mono(audio) -> np.ndarray`
- `normalize_audio(audio) -> np.ndarray`
- WAV/MP3 対応、mono/stereo 両対応
- エラー区別: FileNotFoundError / UnsupportedFormatError / DecodeError

### Step 3: RPE physical extraction
- `physical_features.py`: 各物理指標
  - `compute_rms_mean(y, sr)` — フレーム RMS 平均
  - `compute_active_rate(y, sr, threshold)` — RMS 閾値超えフレーム割合
  - `compute_crest_factor(y)` — peak / RMS
  - `compute_valley_depth(y, sr, method="rms")` — P90 - P10。将来 AR 版差し替え可
  - `compute_thickness(y, sr)` — spectral richness + RMS + valley 簡易複合
  - `compute_spectral_profile(y, sr)` — centroid, low/mid/high ratio, brightness
  - `compute_stereo_profile(y_stereo, sr)` — width, correlation
  - `compute_onset_density(y, sr)` — onset/sec
  - `compute_bpm(y, sr)` — librosa.beat.beat_track
  - `compute_key(y, sr)` — chroma → Krumhansl-Kessler template matching
- `structure.py`: RMS/novelty/onset ベースセグメント分割
  - 最低限 section_01, section_02... を保証（空リスト禁止、validator で強制）
- `extractor.py`: 統合 → PhysicalRPE 構築

### Step 4: RPE semantic generation
- `semantic_rules.py`: 物理特徴 → 意味ラベルの決定論的マッピング
  - ルールは `config/semantic_rules.yaml` から読み込み
  - por_core, por_surface, grv_anchor, delta_e_profile, cultural_context を生成
  - confidence_notes で適用ルールを追跡
  - estimation_disclaimer 固定文言を含む
- `extractor.py` に統合: PhysicalRPE + メタ → SemanticRPE → RPEBundle

### Step 5: SVP generation
- `generator.py`: RPEBundle → SVPBundle（決定論的）
  - 5 ブロック: data_lineage, analysis_rpe, svp_for_generation, delta_E_profile, evaluation_criteria
  - テンプレートベース（`config/svp_templates.yaml`）、語順安定化
  - minimal SVP 同時生成: `{"c": "...", "g": [...], "de": ...}`
  - por_core / grv_anchor / delta_e_profile が SVP に必ず反映される
- `render_yaml.py`: SVPBundle → YAML
- `render_text.py`: SVPBundle → Markdown/TXT

### Step 6: evaluation
- `scorer_rpe.py`: 物理指標 vs Pro 基準値（`config/pro_baseline.yaml`）
  - 各指標を [0,1] スコア化 → RPEScore
- `scorer_ugher.py`: UGHer 系
  - por_similarity: MVP はトークン一致率（interface で将来 embedding 差し替え可）
  - grv_consistency: bpm/key/duration/anchor 一致率
  - delta_e_assessment: energy transition ヒューリスティック
  - physical_accuracy: RPE → SVP 反映率
  - → UGHerScore
- `scorer_integrated.py`: 重み付き統合
  - `integrated_score = w_ugher * ugher + w_rpe * rpe`
  - → IntegratedScore

### Step 7: CLI + export + tests + docs
- `cli.py`: typer ベース
  - `svprpe extract <audio>` → RPE JSON
  - `svprpe generate <rpe.json>` → SVP YAML/TXT
  - `svprpe evaluate --audio <wav> --svp <yaml>` → 評価 JSON
  - `svprpe run <audio>` → 一括
  - `svprpe batch <folder>` → P1 だが骨格作成
  - `--no-save` / `--output-dir` オプション
- テスト: ≥12（正常系 + 異常系）、numpy 合成正弦波で実音源不要
- docs: architecture.md, metrics.md, cli.md
- README.md

---

## Pydantic モデル設計（主要フィールド）

### AudioMetadata
```python
class AudioMetadata(BaseModel):
    schema_version: str = "1.0"
    file_path: str
    duration_sec: float
    sample_rate: int
    channels: int
    format: str  # "wav" | "mp3"
```

### PhysicalRPE
```python
class PhysicalRPE(BaseModel):
    schema_version: str = "1.0"
    bpm: Optional[float] = None
    bpm_confidence: Optional[float] = None
    key: Optional[str] = None
    mode: Optional[str] = None
    key_confidence: Optional[float] = None
    duration_sec: float
    sample_rate: int
    time_signature: str = "4/4"
    time_signature_confidence: float = 0.3
    structure: List[SectionMarker]      # 空リスト禁止 (validator)
    rms_mean: float
    peak_amplitude: float
    crest_factor: float
    active_rate: float
    valley_depth: float
    valley_depth_method: str = "rms"
    thickness: float
    spectral_centroid: float
    spectral_profile: SpectralProfile
    stereo_profile: Optional[StereoProfile] = None
    onset_density: float
```

### SemanticRPE
```python
class SemanticRPE(BaseModel):
    schema_version: str = "1.0"
    por_core: str
    por_surface: List[str]
    grv_anchor: GrvAnchor
    delta_e_profile: DeltaEProfile
    cultural_context: List[str]
    instrumentation_summary: str
    production_notes: List[str]
    confidence_notes: List[str]
    estimation_disclaimer: str = "semantic層はルールベース推定であり、意味理解の真値ではない"
```

### 評価スコア
```python
class RPEScore(BaseModel):
    schema_version: str = "1.0"
    rms_score: float
    active_rate_score: float
    crest_factor_score: float
    valley_score: float
    thickness_score: float
    overall: float

class UGHerScore(BaseModel):
    schema_version: str = "1.0"
    por_similarity: float
    grv_consistency: float
    delta_e_assessment: float
    physical_accuracy: float
    overall: float

class IntegratedScore(BaseModel):
    schema_version: str = "1.0"
    ugher_score: float
    rpe_score: float
    integrated_score: float
    ugher_weight: float = 0.5
    rpe_weight: float = 0.5
```

---

## 物理指標定義

| 指標 | 定義 | 計算式 |
|------|------|--------|
| RMS Mean | フレーム RMS の平均 | `mean(librosa.feature.rms(y))` |
| Active Rate | RMS 閾値超えフレーム割合 | `count(rms > threshold) / total_frames` |
| Crest Factor | ピーク対 RMS 比 | `peak_amplitude / rms_mean` |
| Valley Depth (RMS版) | RMS 分布の動的レンジ | `P90(rms) - P10(rms)` |
| Thickness | 音響密度の複合指標 | `w1*spectral_richness + w2*rms_norm + w3*(1 - valley_norm)` |

---

## ugh-audit-core パターンとの対応

| ugh-audit-core | svp-rpe | 役割 |
|---|---|---|
| `detect()` → `Evidence` | `extract()` → `RPEBundle` | 入力からの事実抽出 |
| `calculate()` → `State` | `generate()` → `SVPBundle` | 事実 → 設計図 |
| `decide()` → verdict | `evaluate()` → scores | 評価・判定 |
| frozen dataclass | Pydantic BaseModel | 不変データ構造 |
| YAML registry | `config/*.yaml` | 外部化された設定・ルール |
| `max(0, min(1, v))` | 同パターン | [0,1] 正規化 |
| None → degraded | Optional + confidence | 欠損時の明示的劣化 |
| `f2_unknown` (weight=25) | instrument_fabrication 概念 | 最重ペナルティ |
| `L_sem` 分解 | UGHer/RPE 二系統分解 | 診断用スコア分離 |

---

## config ファイル

### config/pro_baseline.yaml
```yaml
rms_mean_pro: 0.298
active_rate_ideal: 0.915
crest_factor_ideal: 5.0
valley_depth_pro: 0.2165
thickness_pro: 2.105
```

### config/semantic_rules.yaml
```yaml
rules:
  - condition: {bpm_min: 140, brightness_min: 0.6, active_rate_min: 0.8}
    por_labels: [energetic, driving, dense]
  - condition: {mode: minor, spectral_centroid_max: 2000}
    por_labels: [dark, melancholic, introspective]
  - condition: {valley_depth_min: 0.3}
    por_labels: [dynamic, dramatic, contrastive]
  - condition: {valley_depth_max: 0.1, active_rate_min: 0.8}
    por_labels: [continuous, wall-like, compressed]
  - condition: {stereo_width_min: 0.7}
    por_labels: [spacious, cinematic]
  - condition: {low_ratio_min: 0.4}
    por_labels: [bass-heavy, grounded]
```

---

## 受け入れ条件チェックリスト

| AC | 条件 | 検証方法 |
|----|------|----------|
| AC-01 | ローカルセットアップ | `pip install -e ".[dev]"` |
| AC-02 | `svprpe run <audio>` 成功 | テスト用 WAV で実行 |
| AC-03 | RPE JSON に physical/semantic | JSON キー確認 |
| AC-04 | SVP 決定論性 | 同一入力 3 回 → diff なし |
| AC-05 | SVP に意味核保持 | por_core 文字列一致 |
| AC-06 | 評価 JSON に 3 スコア | ugher_score, rpe_score, integrated_score |
| AC-07 | metrics.md 存在 | docs/metrics.md |
| AC-08 | pytest 通過 (≥10) | `pytest -v` |
| AC-09 | CLI --help | `svprpe --help` |
| AC-10 | エラーメッセージ明確 | 異常系テスト |
| AC-11 | semantic ルール外部化 | config/semantic_rules.yaml |
| AC-12 | Pro 基準値外部化 | config/pro_baseline.yaml |

---

## テスト用音声

リポジトリに外部音源は含めない。テストでは：
- numpy で合成した正弦波 WAV を `tmp_path` に生成
- `conftest.py` に fixture として定義
- examples/ には生成スクリプトを同梱

---

## 検証手順

```bash
# セットアップ
cd svp-rpe
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# テスト
pytest -v

# CLI
svprpe --help
svprpe run <test.wav> --output-dir /tmp/svp_test

# 決定論性
svprpe extract <test.wav> -o /tmp/rpe1.json
svprpe extract <test.wav> -o /tmp/rpe2.json
diff /tmp/rpe1.json /tmp/rpe2.json

# Lint
ruff check src/
```

---

## 成果物一覧

1. 実装済みコード一式
2. README.md
3. docs/architecture.md
4. docs/metrics.md
5. docs/cli.md
6. サンプル入力に対する出力例
7. pytest 実行結果
8. 既知の限界 + 拡張候補（docs/architecture.md 内）

---

## MVP 範囲外（P1/P2）

- structure segmentation 品質向上
- delta_e_assessment 精緻化
- genre テンプレート拡充
- embedding ベース por_similarity
- source separation
- HTML レポート
- batch 本格実装（P0 は骨格のみ）
