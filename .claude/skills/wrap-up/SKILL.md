# /wrap-up — セッション記憶の永続化

セッション終了時に会話全体を振り返り、重要な情報を `.claude/memory/` に保存する。

## 実行手順

1. **会話全体を振り返り、以下の項目を抽出する:**
   - 修正した箇所（Claudeが間違えた部分、ユーザーが訂正した内容）
   - 新しく発見した成功パターン・ベストプラクティス
   - 重要な設計判断とその理由
   - 未解決の課題・次回への引き継ぎ事項
   - CLAUDE.md への反映が必要な変更点

2. **サマリーファイルを作成する:**
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

3. **メタインデックスを更新する:**
   - `.claude/memory/_index.md` に1行サマリーを追記する
   - フォーマット: `- YYYY-MM-DD: （そのセッションの1行要約）`

4. **CLAUDE.md の更新が必要な場合:**
   - サマリーの「CLAUDE.md 更新候補」セクションに内容がある場合、ユーザーに確認の上 CLAUDE.md を更新する

5. **main ブランチに直接 commit & push する:**
   - memory ファイルは運用ログのためレビュー不要
   - 作業ブランチにいる場合は `main` に checkout してから commit する
   - 手順:
     ```bash
     git stash          # 未コミットの作業があれば退避
     git checkout main
     git add .claude/memory/
     git commit -m "memory: セッションサマリー YYYY-MM-DD"
     git push origin main
     git checkout -                # 元のブランチに戻る
     git stash pop      # 退避した作業を復元
     ```

6. **結果をユーザーに報告する:**
   - 保存したファイルパスと内容の要約を表示する
