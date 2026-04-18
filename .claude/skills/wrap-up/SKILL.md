# /wrap-up — セッション記憶の永続化

セッション終了時に会話全体を振り返り、重要な情報を `.claude/memory/` に保存する。

## 実行手順

1. **会話全体を振り返り、以下の項目を抽出する:**
   - 修正した箇所（Claudeが間違えた部分、ユーザーが訂正した内容）
   - 新しく発見した成功パターン・ベストプラクティス
   - 重要な設計判断とその理由
   - 未解決の課題・次回への引き継ぎ事項
   - CLAUDE.md への反映が必要な変更点

2. **サマリー内容をメモリに保持する（まだファイルには書かない）**

3. **main ブランチに切り替える:**
   ```bash
   ORIG_BRANCH=$(git rev-parse --abbrev-ref HEAD)
   [ "$ORIG_BRANCH" = "HEAD" ] && ORIG_BRANCH=$(git rev-parse HEAD)  # detached HEAD 対応
   STASH_COUNT=$(git stash list | wc -l)
   git stash -u        # 未追跡ファイル含め全作業を退避
   git checkout main
   git pull --rebase origin main   # リモートの先行分を取り込む
   ```

4. **main 上でサマリーファイルを作成・コミット・push する:**
   - パス: `.claude/memory/YYYY-MM-DD.md`（同日に複数回実行する場合は `YYYY-MM-DD-2.md`）
   - フォーマット:

   ```markdown
   # Session Summary — YYYY-MM-DD

   ## 修正・訂正
   - （Claudeが間違えた点、ユーザーが訂正した内容）

   ## 成功パターン
   - （うまくいったアプローチ、再利用可能なパターン）

   ## 設計判断
   - （決定事項とその理由）

   ## 未解決課題
   - （次回以降に持ち越す課題）

   ## CLAUDE.md 更新候補
   - （CLAUDE.md に追記すべき内容があれば記載）
   ```

   - `.claude/memory/_index.md` に1行サマリーを追記する
   - フォーマット: `- YYYY-MM-DD: （そのセッションの1行要約）`

   ```bash
   git add .claude/memory/
   git commit -m "memory: セッションサマリー YYYY-MM-DD"
   git push origin main
   ```

5. **README.md 整合性チェック + 半自動更新:**

   main 上で doc_consistency_check を実行し、README.md のずれを検出する:

   ```bash
   bash scripts/doc_consistency_check.sh
   ```

   - 出力なし (exit 0 で何も出ない) → README.md は整合、スキップ
   - 出力あり → 検出項目ごとに以下を判断して対応する:
     - `directory tree missing: <file>` → README.md の「ディレクトリ構成」ツリー
       の適切なセクション (Audit Engine / UGH Audit Layer / 実験基盤 etc.) に
       1 行追加。該当モジュールの役割を日本語で 1 行記述
     - `missing reference to: docs/<file>` → README.md の「設計ドキュメント」
       索引表に 1 行追加。コンポーネント名は対応 docs の h1 タイトルから引用
     - `grv is marked as unimplemented` 等の outdated marker → 現状に合わせた
       記述に書き換え
     - その他のチェック (metadata_source 'fallback'/'computed_ai_draft' 等) →
       該当表に行追加

   **重要:** 自動書き換えではなく、Claude が具体的な変更案を提示して
   user 確認を経てから適用する (README 構造を壊さないため)。
   user が却下したら次のセッションに持ち越す。

   承認が得られた場合のみ commit:

   ```bash
   git add README.md
   git commit -m "docs: README.md を <変更サマリー> で同期 (session wrap-up)"
   git push origin main
   ```

6. **元のブランチに戻り、作業を復元する:**
   ```bash
   git checkout "$ORIG_BRANCH"   # 記録しておいた元のブランチに戻る
   [ "$(git stash list | wc -l)" -gt "$STASH_COUNT" ] && git stash pop --index
   ```

7. **CLAUDE.md の更新が必要な場合:**
   - サマリーの「CLAUDE.md 更新候補」セクションに内容がある場合、ユーザーに確認の上 CLAUDE.md を更新する

8. **結果をユーザーに報告する:**
   - 保存したファイルパスと内容の要約を表示する
   - README.md を更新した場合はそのサマリーも含める
