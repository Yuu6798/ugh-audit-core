#!/usr/bin/env bash
# セッション終了フレーズを検出するフック
# jq がなくても python3 フォールバックで動作する

set -euo pipefail

INPUT=$(cat)

# プロンプトを抽出（jq → python3 フォールバック）
if command -v jq >/dev/null 2>&1; then
  PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty')
else
  PROMPT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))" 2>/dev/null || echo "")
fi

if echo "$PROMPT" | grep -qiE '今日は(ここまで|終わり|おわり)|セッション(終了|閉じ)|また(明日|今度)|お疲れ(様|さま)|done for today|that.s all'; then
  echo 'SESSION_END_DETECTED: /wrap-up を自動実行してください'
fi

exit 0
