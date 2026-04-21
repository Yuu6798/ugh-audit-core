# フェーズロードマップ

ugh-audit-core の実装フェーズ一覧と進捗。Phase 番号は単調増加の時系列系で統一。

## 完了済みフェーズ

- **Phase 1**: スコアリング基盤 + ログ蓄積 — **実装済み**
- **Phase 2**: Audit Engine (detector / calculator / decider) — **実装済み**
- **Phase 3**: reference セット設計 (GoldenStore) — **実装済み**
- **Phase 4**: Phase Map 可視化 + パターン分析 (`ugh_audit/report/phase_map.py`) — **実装済み**
- **Phase 5**: L_sem (意味損失関数) + grv 統合校正 — **実装済み** (HA48 ρ=-0.6020)
- **Phase 6**: `mode_affordance` v1 (response_mode_signal) — **実装済み**
- **Phase 7**: `mode_conditioned_grv` v2 (4 成分解釈ベクトル) — **実装済み** (HA48 anchor_alignment ρ=+0.41)
- **Phase 8**: `verdict_advisory` (mcg → downgrade) — **ship 済み** (n=63 校正、詳細 [`validation.md`](validation.md) §HA63)

## 廃止フェーズ

- ~~**Phase D**: support_signal 要否判断~~ — 独立 phase としては廃止、目的は Phase 8 で吸収達成

## 構造

Audit Engine 本体 (Phase 2) の上に、診断指標 (Phase 5 L_sem)、モード信号
(Phase 6/7)、判定層統合 (Phase 8) が順次積み上げられた。

## 命名履歴

旧 Phase B/C/D/E 命名は 2026-04-21 に 6/7/(D 廃止)/8 へ整理済み。

> 歴史的 file 名 [`docs/phase_e_verdict_integration.md`](phase_e_verdict_integration.md) は
> リンク互換性維持のため rename せず、`Phase E` は `Phase 8` の旧称として
> file 内で相互参照する。

## 主要サブシステム

### grv (因果構造損失) — v1.4 実装済み

`grv = clamp(w_d × drift + w_s × dispersion + w_c × collapse_v2)`

確定重み: w_d=0.70, w_s=0.05, w_c=0.25 (HA48 ρ=-0.357)。SBert 依存。
詳細: [`grv_design.md`](grv_design.md)

### response_mode_signal — v1 実装済み

質問が期待する応答形式 (`mode_affordance`) に対する回答の適合度を測る非破壊信号。
6 modes: definitional / analytical / evaluative / comparative / critical / exploratory。
cue-list ベースの決定的 scorer。verdict に影響しない。
詳細: [`mode_affordance.md`](mode_affordance.md)

### mode_conditioned_grv — v2 実装済み (Phase 7)

grv + mode_affordance から 4 成分解釈ベクトル (anchor_alignment /
collapse_risk / balance / boilerplate_risk) を生成。
詳細: [`grv_design.md`](grv_design.md) §mode_conditioned_grv

### verdict_advisory — ship 済み (Phase 8)

mcg を verdict 層に反映し、accept → rewrite の downgrade を advisory
として発火させる。primary verdict は不変、consumer は任意で採用。
詳細: [`phase_e_verdict_integration.md`](phase_e_verdict_integration.md)
