# PoC 完成チェックリスト（プロジェクト完了マーカー）

本ファイルは `ugh-audit-core` の **PoC フェーズの最終完了条件** を定義する永続タスクトラッカー。
以下 8 項目全ての close をもって「本プロジェクトの PoC 完成」とし、以降は SVP/RPE
または論文投稿など次フェーズに移行する。

- **策定日**: 2026-04-20
- **由来**: PoC 完成度評価レビュー（コード実測 + 外部評価の統合、2026-04-20-3 セッション）
- **根拠**: 全 8 項目は現状資産 + 文書化 + 小規模リファクタで充足可能
  （外部アノテーション追加・新規実験は不要）。実作業 10〜13 時間規模
- **進捗集計**: 0/8 closed（策定時点）

---

## 全体ゴール

「PoC 完成は技術的に達成済み」を **外部査読に対する防御力を持つ形で対外的に宣言できる状態**に
持っていく。1〜5 が完了した時点で「論文投稿の防御力は大幅に上昇」、8 項目全て完了で
「reference implementation として区切り、v0.4.0 タグを打てる状態」。

---

## タスク一覧（ROI 順）

### 1. [ ] ベースライン比較を README に転記（**ROI 最大、1h**）

- **why**: 査読者が最初に見る場所に他手法との比較が無いのは致命的
- **what**: 論文 PDF §5.3 Table 1（BERTScore / BLEURT / HolisticEval2024 / BLEU vs UGHer ρ=0.74）を
  README に転記。HA20 ベースである旨を明示
- **完了条件**: README の「Validation」セクションに比較表が存在し、
  n=20 base / 他手法のソース（論文番号 or URL）が明記されている
- **参照**: 論文 PDF §5.3 Table 1

### 2. [ ] 信頼区間と limitations 開示（**1h**）

- **why**: 「n=48 で楽観的」→「n=48 だが慎重に開示」へ転換、攻撃面消失
- **what**: scipy で ρ=-0.5195 の 95% CI を計算し `docs/validation.md` に併記。
  limitations 節を追加（CI 下限が 0.50 を割る可能性・n 小ささ・single-annotator への言及含む）
- **完了条件**:
  - `docs/validation.md` に `95% CI for system ρ ≈ [?, ?]` が書かれている
  - 「Limitations」節が同ドキュメント内に存在する
  - 点推定と CI 下限の関係について honest に言及されている
- **参照**: `docs/validation.md:10-14`

### 3. [ ] single-annotator 明示（**30m**）

- **why**: 隠していると思われた瞬間に印象が悪化
- **what**: アノテーション関連 doc（`docs/annotation_protocol.md` および
  `docs/validation.md`）に「annotation by single annotator / IRR not measured /
  reference ρ=0.8616 は single-annotator 上限」を明記
- **完了条件**: annotation_protocol.md に single-annotator 声明が含まれ、
  validation.md の reference ρ 脚注に上限性が明記されている
- **参照**: `docs/annotation_protocol.md`, `docs/validation.md:15`

### 4. [ ] HA48 regression test を CI に追加（**2h**）

- **why**: 論文の数字が CI で守られている = reproducibility の最強形
- **what**: `analysis/ha48_regression_check.csv` 読み込み → 全件 audit → saved 値との
  差分 < 1e-4 を assert するテストを新設し、`ci-weekly.yml` に組み込む
- **完了条件**:
  - `tests/test_ha48_regression.py`（または相当ファイル）が存在し green
  - `ci-weekly.yml` で当該テストが実行経路に入っている
  - `docs/validation.md` の ρ 値と CI が連動していることが記述される
- **参照**: `analysis/ha48_regression_check.csv`, `.github/workflows/ci-weekly.yml`

### 5. [ ] `hit_sources` を API 出力トップレベルに昇格（**30m**）

- **why**: deterministic 主張（tfidf-only）と cascade 拡張の区別が外部から見える
- **what**: `Evidence.hit_sources: Dict[int, str]`（`"tfidf" / "cascade_rescued" / "miss"`）を
  `ugh_audit/server.py` / `ugh_audit/mcp_server.py` の response JSON トップレベルに
  `hit_sources_summary: {tfidf: N, cascade_rescued: M, miss: K}` として追加
- **完了条件**:
  - REST API / MCP の 2 系統で `hit_sources_summary` が返却される
  - `tests/test_server.py` / `tests/test_mcp_server.py` で shape 検証が追加される
  - `docs/server_api.md` にフィールド記載
- **参照**: `Evidence.hit_sources`（`detector.py`）, `docs/server_api.md`

### 6. [ ] 主指標政策の明示（ΔE_A 主 / L_sem 診断）（**30m**）

- **why**: ユーザー/査読者が「結局どちらを見ればよいか」で迷うのを解消
- **what**: `semantic_loss.py:34-47` の「LOO-CV shrinkage=0.128」コメントを
  README および `docs/validation.md` に格上げし、「主指標は ΔE_A、L_sem は診断用分解指標」を
  1 段落で明文化
- **完了条件**: README と validation.md の両方に「主指標政策」節が存在する
- **参照**: `semantic_loss.py:34-47`, `docs/semantic_loss.md`

### 7. [ ] README 582 行 → 200 行目標に圧縮（**2-3h**）

- **why**: 査読者の第一印象を整える
- **what**: 以下に切り出し（または既存 doc に統合）:
  - 確定パラメータ一覧 → `docs/thresholds.md` に統合済みなら参照のみ残す
  - Phase ロードマップ → `docs/roadmap.md` 新設 or 既存 docs に統合
  - 設計ドキュメント表 → footer に圧縮 or `docs/index.md`
  - トラブルシューティング → `docs/troubleshooting.md`
  - 環境変数 → `docs/server_api.md` に既出、README からは削除
- **完了条件**: README が 200 行前後、削除された内容の移管先 doc が全て存在する

### 8. [ ] L_sem 7 項のオーバーエンジ整理 + Phase 命名統一（**2-3h**）

- **why**: 余裕があれば。ドキュメント肥大化と命名混乱の解消
- **what**:
  - **8a**: L_sem 7 項のうち HA48 で信号無しの `L_A` / `L_X` について、「3 項版を主、
    7 項は appendix」として整理。後方互換で `DEFAULT_WEIGHTS` を保持する場合は保持理由を明記
  - **8b**: Phase B/C/D/E の混在命名を統一（例: Phase 6 mode_affordance /
    Phase 7 mode_conditioned_grv / Phase 8 欠番 or 廃止 / Phase 9 verdict_advisory）。
    README と CLAUDE.md の grep 一括置換
  - **8c**: 13 opcode 未評価については「副産物、別論文の射程」と `docs/` に protocol plan だけ書く
- **完了条件**: Phase 命名が README/CLAUDE.md で一貫、L_sem 主要 3 項が README で前面化、
  opcode scope 外声明がドキュメントに存在する
- **参照**: `docs/semantic_loss.md`, `docs/validation.md:67-76`

---

## 完了時のアクション

8 項目全 close で:

1. `CHANGELOG.md`（無ければ新設）に「v0.4.0: PoC 完成マイルストーン」を記録
2. `git tag v0.4.0` を打つ（main ブランチで、ユーザー承認後）
3. `_index.md` に「PoC 完成宣言」セッション記録を追加
4. 次フェーズ移行（SVP/RPE 着手 or 論文投稿）の判断を別セッションで実施

## 備考

- タスク 3 (single-annotator 明示) はユーザー確認を要する性質（single-annotator であることが
  事実であることの最終確認）。着手前にユーザー承認を推奨
- タスク 8 は他タスクと比べ工数大、scope cut して後送りしても全体完了の妨げにならない
- タスク 1/5 はコード変更を伴うので PR レビュー経路を通す必要がある
- タスク 2/3/6 は docs のみの変更なので `.claude/memory/` 同様に直接 commit 可能
