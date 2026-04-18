# Session Memory Index

セッションサマリーの一覧。新しいセッション起動時にこのファイルを最初に参照する。

## セッション一覧

- 2026-04-10: Advisor Strategy + 永続記憶 Phase 1 実装、PR #57 レビュー対応6件（hook スキーマ修正、git ワークフロー堅牢化、誤発火防止）
- 2026-04-10-2: 意味損失関数 L_sem Phase 0-4 完全実装 (PR #59 マージ済み、ρ=-0.5563 で ΔE を上回る)、Codex レビュー 9件対応、CLAUDE.md 596→294 行スリム化 + docs/ 4 新規ファイル + ドキュメント管理ポリシー明文化
- 2026-04-11: PR #60/#61 マージ (MempPalace inspired: GoldenStore 2段検索 + 永続埋め込みキャッシュ + Self-Audit Principle)。Codex レビュー計 18 件対応 (caching state management 11 件 + Phase 1 infrastructure 7 件)。CLAUDE.md に Self-Audit Principle 追加 + 行上限 300→400。Phase 1 self-audit 実測基盤 (analysis/self_audit_session.py + extract_claude_transcript.py) 構築、実セッションで測定して negative result (H1 部分支持、H2/H3 否定) を docs/self_audit_experiment.md に記録。Phase 1 凍結、次回は意味監査ツール本体へ
- 2026-04-11-2: verdict/mode 型安定化 (derive_verdict/derive_mode 集約 + VALID_VERDICTS/VALID_MODES) + is_reliable fail-closed フラグ (gate_verdict!=fail 追加) + f3 なんで/なんです衝突修正。PR #63。metadata_generator/soft_rescue/metadata_policy の WIP 追加。Codex レビュー 2件対応
- 2026-04-12: metadata_generator / soft_rescue パイプライン統合 + computed_ai_draft mode + disqualifying_shortcuts 自己矛盾修正。PR #65 マージ (レビュー 13 ラウンド対応)。/simplify で定数一元化 (GATE_FAIL / META_SOURCE_*) + soft_rescue tokenization 最適化 + dead code 削除。docs/metadata_pipeline.md 新規作成 + CLAUDE.md 更新
- 2026-04-13: Railway VPS デプロイ完了 (常時稼働 https://ugh-audit-core-production.up.railway.app)。DB参照全経路対応 (REST API 3ep + MCP 4ツール + CLI)。verdict/mode 型安定化 + is_reliable fail-closed。f3 なんで/なんです衝突修正。MCP プロキシモード (UGH_REMOTE_API) で DB 一本化。PR #63/#66/#67 マージ。Codex レビュー計6件対応
- 2026-04-14: grv v1.2→v1.3→v1.4 を1セッションで完走。v1.2: entropy型collapse死亡 (σ=0.002)。v1.3: 2comp確定 (ρ=-0.318)、collapse方向逆転で除外。v1.4: residual型collapse_v2 + cover_soft + wash_index で ρ=-0.357 に改善、V-4 PASS。確定重み w_d=0.70/w_s=0.05/w_c=0.25。PR #68/#69/#70 マージ。Codex レビュー計23件対応。判定層ロードマップ Phase B〜E 設計確定
- 2026-04-14-2: auto_generate_meta 不具合の根本原因特定・修正。`.dockerignore` が `experiments/` を除外していたのが真因。PR #72〜#75 (6件のレビュー対応含む)。`META_SOURCE_FALLBACK` 新設、フォールバック degraded 強制、`is_reliable=false`、`core_propositions` 由来ゲート。本番 LLM メタ生成 + キャッシュ動作確認済み
- 2026-04-15: Phase B mode_affordance v1 完全実装。6 modes + response_mode_signal (cue-list 決定的 scorer) + 102問ラベル + canonical lookup。PR #77 マージ (Codex レビュー計11件対応)。docs/schema 整備。565 tests passed
- 2026-04-15-2: f4_trap_type_missing 常時発火バグ修正 (PR #76)。mode_affordance v1 設計統合 (GPT アドバイザー活用)。evaluative/critical 分離・procedural 延期・lookup 優先順位・grv 合成禁止を確定。SessionStart hook (doc_consistency_check.sh) 導入。PR #77 レビュー完了 (マージ可)
- 2026-04-15-3: SVP/RPE 音楽生成ツール概念設計。ugh-audit-core の PoR/ΔE/grv パターンを音楽ドメインに射影し統合実装プランを完成。結論: RPE の第一消費先は「ユーザーの好みの構造化」。既存競合分析 (Essentia 等) で新規価値は SVP 変換層+評価統合層と特定。実装は意味監査基盤が固まってから
- 2026-04-16: L_sem Phase 5 grv 統合校正 (ρ=-0.6020, LOO-CV 補正で L_G=0.35)。Phase C mode_conditioned_grv v2 完全実装 (4成分解釈ベクトル + API 統合)。grv タグ閾値校正 (0.33/0.66→0.20/0.30)。PR #79 マージ (Codex レビュー計10件対応)。不要ファイル整理 (v1.3スクリプト+旧CSV+完了タスク仕様 削除)。589 tests passed
- 2026-04-17: karpathy-guidelines スキル取り込み (PR #81)。memory 乖離発見→Phase 5+Phase C 未マージを回収 (PR #82, Codex 2件)。Phase E verdict_advisory 設計→ローカル実装→PR #84 マージ (no-ship 判定で plumbing のみ)。HA-accept40 拡充 tooling (sampler/ui/merge/blind_check/incremental_cal) Opus 代打実装 (PR #85, 7ラウンド・Codex 計14件対応中)。canonical ΔE を 3 ファイルで線形コピーしていた latent bug を ugh_calculator import 一本化で解消。doc_consistency_check を SessionStart→SessionEnd に変更 (直 commit 77c5498)。ロール分担 (Opus 設計 / ローカル実装) 確立 + ローカル停止時の Opus 臨時代打パターン確認
- 2026-04-18: PR #85/#86 マージ後検証。canonical ΔE import の生存確認（analysis 3 ファイルで一本化）を実施、`calibrate_phase_e_thresholds.py` の local wrapper を削除。proxy 環境変数 (`127.0.0.1:9`) により SBert ロードが劣化して leak n=0 になる退行を特定し、proxy 解除 + HF offline cache で再校正を再実行。n=63 で `τ_collapse_high=0.28`, `τ_anchor_low=0.80`, `fire_rate=0.225`, `ρ_adv=0.5225` を確認し Branch A（ship）判定に更新。mode_grv/CLAUDE.md/Phase E docs を n=63 ステータスへ同期。

