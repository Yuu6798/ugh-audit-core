# Backlog — PoC 完成チェックリスト外の残タスク

本ファイルは `_poc_completion_checklist.md` の 8 項目とは **別枠** で、
メモリ（セッションサマリー）由来の残タスクを永続保持するバックログ。

## 重要: 全レーン「着手前提」

**本バックログ記載のタスクは deferred / optional / nice-to-have ではない。
ユーザー方針として全件着手する前提で運用する。**

PoC 完成チェックリスト 8 項目を優先的に close するのは「論文投稿防御力の到達」が
最も ROI 高いため。PoC 完成宣言の前後・並行で本バックログ B/C/D/E 各レーンにも着手する。
「PoC 完成で終わり」ではなく「PoC 完成は通過点、本バックログ完遂まで継続」が方針。

- **策定日**: 2026-04-20（セッション 2026-04-20-3）
- **出典セッション**: 2026-04-18-2 / 2026-04-18-3 / 2026-04-20 / 2026-04-20-2
- **位置づけ**: PoC 完成チェックリストと並行で進める継続的 backlog

---

## レーン B: PoC と並行で進む運用・設計タスク

| ID | タスク | 状態 | 工数感 | 出典 |
|---|---|---|---|---|
| B1 | **AuditCollector degraded 固定バグ修正** — `collector.collect()` が `Evidence(question_id="unknown")` を直接 `calculate()` に渡し常に degraded 返す。test 4 件が挙動を仕様 lock、test 書換 + 公開 API 動作変更 (semver minor bump 相当) 必要 | [ ] | 半日〜1日（独立セッション推奨） | 2026-04-20, 2026-04-20-2 |
| B2 | **`ci-weekly.yml` 初回手動 kick** — merge 後 `workflow_dispatch` で SBert DL + 7 skip テスト pass 確認 | [ ] | 5 分（ユーザー作業） | 2026-04-20-2 |
| B3 | **verdict_advisory 本番 telemetry 観察** — Railway で 24–72h 蓄積し τ=0.28/0.80 本番分布妥当性確認。accept subset n ≥ 80 到達で再校正トリガ | [ ] | 観察 48h + 分析 1-2h | 2026-04-18-2 |
| B4 | **Phase D (support_signal) 要否確定** — Phase E ship で positive-evidence の一部が解決。結論候補: 不要 / rewrite→accept rescue 再配置 / 別目的で残す | [ ] | 設計議論 2-3h（Opus 向き） | 2026-04-18-2 |

---

## レーン C: データ拡充 3-phase 戦略

各 phase 完了時に「続行 or 別軸転換」の中間判断ポイントを設ける。

| ID | タスク | 目的 / 完了条件 | 工数 | 出典 |
|---|---|---|---|---|
| C0 | **sampler `--polarity-focus` / `--borderline-focus` オプション追加** | Phase 1/2 サンプリング用の直近着手タスク | 1-2h | 2026-04-18-3 |
| C1 | **HA100 Phase 1 annotation (+37 件)** | Phase E 閾値 robustness 確認（τ=0.28 ±0.02 以内で安定 / accept subset n≥60） | 2-3h | 2026-04-18-3 |
| C2 | **HA130 Phase 2 annotation (+30 件)** | L_X polarity signal 検出（polarity-bearing 命題 n≥50 / ρ<-0.20 or signal なし確定） | 1.5h | 2026-04-18-3 |
| C3 | **HA200 Phase 3 annotation (+70 件)** | boilerplate_risk + balance + 多様性検証（学術 standard zone 上端到達） | 4-5h | 2026-04-18-3 |

**依存**: C0 → C1 → C2 → C3 の順序で実施。C1 到達時点で B3 (telemetry) の再校正トリガ
（accept subset n≥80）との合流判定を行う。

---

## レーン D: PoC 後の横展開

PoC 完成チェックリスト 8/8 close 後、または本バックログ B/C 進行と並行で着手。

| ID | タスク | 出典 |
|---|---|---|
| D1 | **SVP/RPE 音楽ドメイン射影 実装着手** — 2026-04-15-3 概念設計済、`docs/svp_rpe_implementation_plan.md` 参照 | 2026-04-15-3, 2026-04-18-2 |
| D2 | **Cross-model 評価 (Gemini / Llama / Mistral)** — experiments/ は現在 Claude+GPT の 2-model | 2026-04-20-2, 2026-04-18-3 |
| D3 | **多言語対応 (英語 registry / cascade)** | 2026-04-18-3 |
| D4 | **解釈 UI (advisory_flags 可視化)** | 2026-04-18-3 |
| D5 | **4 原則の外向き設計論 `docs/tool_ecosystem_design.md`** — 論文接続の新射程、論文側文脈確定後 | 2026-04-20-2 |

---

## レーン E: CLAUDE.md 運用知見の昇格候補

PoC チェックリスト T7 (README 圧縮) / T8 (Phase 命名統一) と同時処理推奨。
CLAUDE.md が 400 行上限に近いため、1-2 件に絞るか `docs/` 分離を検討。

| ID | 昇格候補 | 出典 |
|---|---|---|
| E1 | **Single Source of Truth 原則** — runtime `.py` が真値、markdown doc は索引参照 | 2026-04-20-2 |
| E2 | **scope pivot パターン** — 全を謳う promise → narrow + 判断基準 + 具体追記 | 2026-04-20-2 |
| E3 | **E2E install verify** — wheel 同梱保証の `build→venv→import` 手順 | 2026-04-20-2 |

---

## 進捗サマリ

- レーン B: 0/4 closed
- レーン C: 0/4 closed（C0 → C1 → C2 → C3 順序依存）
- レーン D: 0/5 closed
- レーン E: 0/3 closed
- **合計: 0/16 closed**（策定時点）

PoC 完成チェックリスト (`_poc_completion_checklist.md`): 0/8 closed

**累積タスク総数: 24 件**（完全 close 時点で本リポジトリの PoC 完成宣言 + 横展開 or 論文投稿フェーズ移行）

---

## 運用ルール

1. タスクを close する際は該当セッションサマリーでクロージャを記録
2. 新規タスク発生時は本ファイル該当レーンに追記（レーンが該当しない場合は新レーン立てる）
3. 本ファイルは `_poc_completion_checklist.md` と独立して更新（片方の進捗が他方をブロックしない）
4. 全 24 件 close 時に `_index.md` に「PoC 完成 + backlog 完遂宣言」セッションを記録
