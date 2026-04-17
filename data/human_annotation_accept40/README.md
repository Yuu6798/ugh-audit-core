# human_annotation_accept40

Phase E 閾値再校正のための accept-verdict subset 拡充データ。HA48 の accept サブセット n=13 を n ≥ 28 まで増やすことを目的とする。

## 目的

`analysis/calibrate_phase_e_thresholds.py` が `verdict_advisory` の τ 閾値を確定するには accept subset の n が少なくとも 28（HA48 の 13 + 新規 15 以上）必要。本データセットはその差分を埋める。

## データ範囲

- 候補ソース: priority A (`data/eval/audit_102_main_baseline_v5.csv` の未アノテート分で ΔE ≤ 0.15) + priority B (experiments/orchestrator.py で新規生成した回答から accept 相当を抽出)
- production audit.db 由来は含めない（本拡充ではスコープ外）

## スキーマ

`annotation_accept40.csv`:

| カラム | 型 | 説明 |
|---|---|---|
| id | str | `acc40_NNN` 形式（HA48 の `qNNN` と衝突しない prefix） |
| source | str | `v5_unannotated` / `v5_borderline` / `orchestrator_claude` / `orchestrator_gpt4o` |
| question_id | str | 元の質問 ID（q001–q102 のいずれか） |
| question | str | 質問本文 |
| response | str | AI 回答本文 |
| core_propositions | str | JSON 配列（質問 meta から引用） |
| O | int | 1–5 Likert（HA48 と同一スケール、新規分は `annotation_ui.py` が書き込み） |
| rater | str | アノテータ識別子 |
| annotated_at | str | ISO 8601 UTC |
| comment | str | template 選択結果＋詳細（`annotation_ui.py` で記録） |
| blind_check | str | HA48 からの混入なら元 id、新規なら空 |
| hits_total | str | "N/M" 形式（UI 表示用ヒント、O への anchoring を避けるため hits のみ表示） |

## アノテーション手順

詳細: [`docs/annotation_protocol.md`](../../docs/annotation_protocol.md)

## 利用条件

本データは ugh-audit-core リポジトリと同じ MIT ライセンス。rater は自己申告ベースで記録（単独 rater でも可）。
