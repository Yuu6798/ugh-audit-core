# ugh-audit-core

**UGH Audit Core** は、明示された要件が回答文に伝えられているかを要件ごとに照合し、
根拠と判定を記録するツール。現在の契約は **schema 4.0.0 / `instruction_coverage_v2`**。
この作業版はローカルの修正であり、公開環境への反映を意味しない。

## 判定する対象

人が用意した `question_meta.core_propositions` を基準に、回答が伝えるべき業務の
指示・制約の記載を点検する。同義の言い換えを認め、主体・対象・数量・日時・条件・
義務・禁止の極性を照合する。要件と回答の正しさ、現実の業務の実行、回答全体の品質を
認定するものではない。禁止指示の記載が必要な課題で、禁止行為が書かれていないだけでは
充足にならない。完成した成果物の規則遵守を判定する別の課題へそのまま一般化しない。

各要件を `fulfilled` / `missing` / `unknown` に分類する。`missing` は単純欠落
(`absence`) または明示矛盾 (`contradiction`)。単に書かれていない情報は欠落であり、
曖昧で読みを確定できない場合やシステム障害は保留となる。

| 照合結果 | 最終 `verdict` | 上位 `C` |
|---|---|---|
| 1件以上の要件がすべて fulfilled | `accept` | 1.0 |
| unknown がなく、missing がある | `rewrite` | fulfilled / 全要件数 |
| unknown がある、要件がない・不正、照合不能 | `degraded` | null |

上位 `S`・`delta_e`・`quality_score` は常に null。`C` は全要件を判定できた場合の
記述充足率であり、回答品質や事実の正確さのスコアではない。保留を分母から除かない。
`decision_scope="instruction_coverage_v2"`、`metrics_scope="semantic_coverage_only"`。
この契約では `regenerate` を最終判定として生成しない。

`requires_review` は欠落・保留がある場合に加え、AI が生成した要件メタデータにも true。
AI 草案を使った `accept` も、基準となる要件自体のレビューが必要である。
`is_reliable` は照合結果が確定したことを示し、真実性や無条件の承認を表さない。

## 実行経路と診断値

REST、ローカル MCP、Python の `audit.audit()` と CLI は共有の
`ugh_audit.pipeline.run_audit()` を使う。

1. 明示要件を準備する。`auto_generate_meta=true` は不足要件を AI 草案として補う opt-in。
2. 必須の意味照合を実行する。要件準備後に 25 秒の予算を開始し、1要件は残り時間以下・
   最大10秒。任意の埋め込み・旧検出器より先に行う。
3. 旧 `detect → calculate → decide`、SBert、grv 等は任意の診断として計算する。
4. 検証済みの要件照合だけから上位の充足率と最終判定を返す。

旧数値・旧 verdict は `legacy_metrics` に残す。構造ゲートは `binding=false`。
旧値や advisory は最終判定を上下させず、診断の失敗も確認済みの要件充足を取り消さない。
数値式と重みは [`docs/formulas.md`](docs/formulas.md) に保存している。
これらを加えると単純な LLM 監査より有用になるという根拠は、この契約からは主張しない。

意味照合にはサーバー環境の `ANTHROPIC_API_KEY` が必要。認証情報不足、API障害、時間切れ、
不正JSON、架空引用、モデルの不一致を充足扱いにしない。1回答16要件、回答32,000文字、
1要件2,000文字を上限とし、一部だけを捨てて合格にしない。SBert は任意の診断用依存である。

Python の修復順序も意味照合に基づく。`rewrite` の確認済み欠落ごとに `ADD_PROPOSITION`、
最後に `STOP_REWRITE` を返す。旧 f1〜f4・数値から修復を追加しない。保留時に充足を補完した
ことにしたり、実際の修復効果を保証したりする機能ではない。

## schema 4.0.0 への移行

schema 3 の「旧数値判定を意味ゲートで抑止する」契約からの破壊的変更である。
上位の `S`・`delta_e`・`quality_score` の数値、旧 `C`、旧 structural gate を承認条件に
使うクライアントは更新が必要。現行の `C` と要件別根拠を読む。
履歴・remote proxy も同じ契約で再検証し、旧 accept をそのまま再公開しない。
元の保存値は `legacy_metrics` に残し、現在の照合根拠を確認できない履歴は degraded とする。
詳細: [`docs/semantic_matching_v2.md`](docs/semantic_matching_v2.md)、
[`docs/server_api.md`](docs/server_api.md)。

## 旧数値の検証記録

以下は **2026-04 の旧 pipeline** に対する保存記録であり、v4 の品質保証、
単純な LLM 監査への上乗せ価値、独立評価の根拠ではない。

| 旧指標 (HA48, n=48) | Spearman ρ | 95% CI (Fisher z) |
|---|---|---|
| ΔE vs O (system C) | -0.4817 | [-0.6736, -0.2289] |
| L_sem vs O (Phase 5) | -0.5477 | [-0.7198, -0.3121] |
| ΔE vs O (human C) | +0.8616 | [+0.7647, +0.9204] |

小標本・単一評価者の制約がある。修正前 HA48 CSV は完全保存し、既知データの現行
legacy 診断は 96/146 hits。数値と版の対応: [`docs/validation.md`](docs/validation.md)、
[`docs/ha48_snapshot_versions.md`](docs/ha48_snapshot_versions.md)。

---

## インストール

Python 3.10+ を前提とする。仮想環境を推奨:

```bash
python -m venv .venv
source .venv/bin/activate           # Linux / macOS
# .venv\Scripts\activate            # Windows PowerShell

pip install -e ".[dev]"        # 基本（テスト + サーバー依存）
pip install -e ".[server]"     # サーバーデプロイ (REST API + MCP)
pip install -e ".[analysis]"   # 分析スクリプト (scipy + matplotlib)
pip install -e ".[experiment]" # 実験基盤 (Claude/GPT オーケストレーション)
```

PEP 668 環境では仮想環境での install 必須。詳細は
[`docs/troubleshooting.md`](docs/troubleshooting.md) 参照。

---

## クイックスタート

### CLI（Audit Engine）

`core_propositions` は回答が伝えるべき指示として人が定義する。
既存データ中の哲学的・一般的な問いは、現在の記述監査の性能根拠にはしない。

```bash
python audit.py --question-id q001 --response "AIは..." \
    --data data/question_sets/ugh-audit-100q-v3-1.jsonl --pretty
```

### REST API

```bash
uvicorn ugh_audit.server:app --host 0.0.0.0 --port 8000
```

```python
import httpx

# 人が定義した指示要件
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "書類送付の案内を作成してください。",
    "response": "申請書は金曜日までに担当者へ送付してください。",
    "question_meta": {
        "core_propositions": ["申請書は金曜日までに担当者へ送付する"]
    }
})

# AI 草案要件を使う場合は、要件自体のレビューが必要
resp = httpx.post("http://localhost:8000/api/audit", json={
    "question": "書類送付の案内を作成してください。",
    "response": "申請書を担当者へ送付してください。",
    "auto_generate_meta": True,
})
```

REST / MCP API 仕様の全詳細: [`docs/server_api.md`](docs/server_api.md)。

---

## ディレクトリ構成

```
# Audit Engine（要件照合と任意の旧診断）
audit.py              # 共有の要件照合 + 意味上の欠落に基づく修復順序
detector.py           # 検出層 — テキスト → Evidence
ugh_calculator.py     # 電卓層 — Evidence → State (S, C, ΔE, quality_score)
decider.py            # 判定層 — State + Evidence → Policy
cascade_matcher.py    # 回収補助 — SBert Tier 2/3 (optional)
grv_calculator.py     # 因果構造損失 grv (drift/dispersion/collapse)
mode_signal.py        # 応答モード適合度信号 response_mode_signal
mode_grv.py           # mode_conditioned_grv v2 (Phase 7/8)
semantic_loss.py      # 意味損失関数 L_sem (診断用)
batch_audit_102.py    # 102問一括監査
registry/             # YAML辞書（予約語・演算子・前提フレーム）
opcodes/              # 修復opcode定義

# UGH Audit Layer（REST/MCP サーバー + 永続化）
ugh_audit/{server,mcp_server}.py         # REST API + MCP
ugh_audit/{pipeline,semantic_matching}.py # schema 4 の共有要件照合
ugh_audit/{collector,storage,reference,report,engine}/
ugh_audit/{metadata_generator,metadata_policy,soft_rescue}.py

# 実験基盤（LLM オーケストレーション）
experiments/{meta_generator,meta_cache,response_source,orchestrator,validate_against_102}.py

examples/ tests/ docs/ analysis/
```

---

## Public API

```python
from ugh_audit import (
    AuditDB,
    GoldenStore, GoldenEntry,
    generate_text_report, generate_csv,
)
```

プログラムから監査を行う場合は REST `POST /api/audit` または MCP ツール
`audit_answer` を利用する (詳細は [`docs/server_api.md`](docs/server_api.md))。

> **Deprecated (v0.4, removal v0.5)**: `AuditCollector` / `SessionCollector` は
> `question_meta` を受け取らないため常に `verdict="degraded"` を返す欠陥 API。
> import すると `DeprecationWarning` が発生する。v0.5 で削除予定。

---

## MCP サーバー（ChatGPT Connectors 対応）

ChatGPT から `audit_answer` ツールを呼び出せる MCP (Model Context Protocol)
サーバーを内蔵。`stateless_http` モードで動作。

```bash
pip install -e ".[server]"
python -m ugh_audit.mcp_server                 # スタンドアロン (port 8000)
uvicorn ugh_audit.server:app --port 8000       # REST + MCP 統合
# → MCP:  http://localhost:8000/mcp
# → REST: http://localhost:8000/api/audit, /api/history
```

API 仕様 (tool schema / エンドポイント / 環境変数) 全詳細:
[`docs/server_api.md`](docs/server_api.md)。
トラブルシューティング: [`docs/troubleshooting.md`](docs/troubleshooting.md)。

---

## 設計ドキュメント

| コンポーネント | ドキュメント |
|---|---|
| 検出層 (演算子フレーム + Relaxed Tier1) | [`docs/detector_design.md`](docs/detector_design.md) |
| Cascade Matcher (SBert Tier 2/3) | [`docs/cascade_design.md`](docs/cascade_design.md) |
| GoldenStore リファレンス検索 | [`docs/golden_store.md`](docs/golden_store.md) |
| 現行の要件照合契約 (schema 4) | [`docs/semantic_matching_v2.md`](docs/semantic_matching_v2.md) |
| schema 4 の検証・移行・保存証拠 | [`docs/instruction_coverage_v4_validation.md`](docs/instruction_coverage_v4_validation.md) |
| 旧診断の計算式 (PoR / ΔE / verdict) | [`docs/formulas.md`](docs/formulas.md) |
| 意味損失関数 L_sem | [`docs/semantic_loss.md`](docs/semantic_loss.md) |
| grv 因果構造損失 | [`docs/grv_design.md`](docs/grv_design.md) |
| LLM オーケストレーション | [`docs/orchestration_design.md`](docs/orchestration_design.md) |
| mode_affordance / response_mode_signal | [`docs/mode_affordance.md`](docs/mode_affordance.md), [`addendum`](docs/mode_affordance_v1_addendum.md) |
| メタデータパイプライン | [`docs/metadata_pipeline.md`](docs/metadata_pipeline.md) |
| REST API + MCP サーバー | [`docs/server_api.md`](docs/server_api.md) |
| 検証結果 (HA48 / HA20 + CI + Limitations) | [`docs/validation.md`](docs/validation.md) |
| Self-Audit 実験 | [`docs/self_audit_experiment.md`](docs/self_audit_experiment.md) |
| Phase 8 verdict_advisory 統合 | [`docs/phase_e_verdict_integration.md`](docs/phase_e_verdict_integration.md) |
| HA-accept40 アノテーションプロトコル | [`docs/annotation_protocol.md`](docs/annotation_protocol.md) |
| 閾値一覧と導出根拠の索引 | [`docs/thresholds.md`](docs/thresholds.md) |
| フェーズロードマップ + サブシステム紹介 | [`docs/roadmap.md`](docs/roadmap.md) |
| トラブルシューティング | [`docs/troubleshooting.md`](docs/troubleshooting.md) |
| SVP-RPE 統合実装プラン（関連プロジェクト） | [`docs/svp_rpe_implementation_plan.md`](docs/svp_rpe_implementation_plan.md) |
| 修復 opcode 評価プロトコル (plan, scope 外) | [`docs/opcode_evaluation_plan.md`](docs/opcode_evaluation_plan.md) |

---

## 理論背景

- [無意識的重力仮説（UGHer）](https://note.com/kamo6798/n/n5aeea478d12e)
- [RPE入門](https://note.com/kamo6798/n/n99cbb5307e13)
- [SVPとRPEの実践メモ](https://note.com/kamo6798/n/nb45c716a2c61)
- [ugh3-metrics-lib](https://github.com/Yuu6798/ugh3-metrics-lib)

---

## License

MIT License (C) 2025 Yuu6798
