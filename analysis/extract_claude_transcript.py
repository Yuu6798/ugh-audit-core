"""analysis/extract_claude_transcript.py — Claude Code の .jsonl セッションログを
self_audit_session.py で読める形式に変換する。

### 抽出ルール

Claude Code の jsonl 形式では、1 会話ターンが複数のレコードに分散する:

- "user" type で content が str → 実ユーザー入力 (transcript に含める)
- "user" type で content が list (tool_result) → ツール出力 (スキップ)
- "assistant" type で content が list → text / thinking / tool_use ブロックを含む
  - text ブロックのみ抽出、thinking と tool_use はスキップ

ユーザーの 1 入力に対して、次のユーザー入力までに含まれる全 assistant text
ブロックを連結して 1 assistant ターンとして出力する。

### 出力形式

self_audit_session.py が期待する形:
```json
[
  {"turn": 1, "role": "user", "content": "..."},
  {"turn": 2, "role": "assistant", "content": "..."},
  ...
]
```

### 使い方

```
python analysis/extract_claude_transcript.py \\
    --session ~/.claude/projects/-home-user-ugh-audit-core/<uuid>.jsonl \\
    --output transcript.json
```
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _is_real_user_input(rec: Dict) -> Optional[str]:
    """user レコードから実ユーザー入力のテキストを抽出する。

    content は str / list のいずれかで、list の場合は text と tool_result が
    同じ message 内に混在することがある (Claude API のメッセージ仕様)。
    その場合でも text ブロックは実ユーザー入力なので拾う必要がある
    (Codex review PR #61 r3067358382)。

    ルール:
    - content が str → そのまま返す
    - content が list → text ブロックを全部連結して返す。tool_result ブロック
      は無視 (混在していても text があれば拾う)
    - text ブロックが 1 つも無ければ None (ツール出力だけの user レコード)
    """
    if rec.get("type") != "user":
        return None
    msg = rec.get("message", {})
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            # tool_result は無視。text と混在していても text を drop しない。
        if texts:
            combined = "\n".join(t for t in texts if t.strip())
            if combined.strip():
                return combined
    return None


def _extract_assistant_text(rec: Dict) -> str:
    """assistant レコードから text ブロックのみ連結して返す。"""
    if rec.get("type") != "assistant":
        return ""
    msg = rec.get("message", {})
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: List[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        # thinking, tool_use, signature などは含めない
    return "\n".join(parts).strip()


def extract_transcript(jsonl_path: Path) -> List[Dict[str, Any]]:
    """jsonl を conversation turn の list に変換する。"""
    turns: List[Dict[str, Any]] = []
    current_assistant_chunks: List[str] = []
    turn_counter = 0

    def _flush_assistant():
        """累積 assistant text を 1 turn としてコミット。"""
        nonlocal turn_counter
        if not current_assistant_chunks:
            return
        combined = "\n\n".join(c for c in current_assistant_chunks if c.strip())
        if combined.strip():
            turn_counter += 1
            turns.append({
                "turn": turn_counter,
                "role": "assistant",
                "content": combined,
            })
        current_assistant_chunks.clear()

    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = rec.get("type", "")

            if rtype == "user":
                user_text = _is_real_user_input(rec)
                if user_text is None:
                    continue  # tool_result 等はスキップ
                # 直前の assistant chunks を flush してから user を追加
                _flush_assistant()
                turn_counter += 1
                turns.append({
                    "turn": turn_counter,
                    "role": "user",
                    "content": user_text,
                })

            elif rtype == "assistant":
                text = _extract_assistant_text(rec)
                if text:
                    current_assistant_chunks.append(text)

            # system / attachment / queue-operation は無視

    # 最後の assistant chunks を flush
    _flush_assistant()

    return turns


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Claude Code の jsonl セッションログから transcript を抽出",
    )
    parser.add_argument(
        "--session",
        required=True,
        type=Path,
        help="Claude Code の .jsonl セッションファイルパス",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="出力先 JSON ファイル",
    )
    args = parser.parse_args()

    if not args.session.exists():
        print(f"Error: session file not found: {args.session}", file=sys.stderr)
        return 1

    turns = extract_transcript(args.session)
    if not turns:
        print("Warning: no turns extracted", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(turns, f, ensure_ascii=False, indent=2)

    n_user = sum(1 for t in turns if t["role"] == "user")
    n_assistant = sum(1 for t in turns if t["role"] == "assistant")
    total_chars = sum(len(t["content"]) for t in turns)
    print(f"Extracted {len(turns)} turns ({n_user} user, {n_assistant} assistant)")
    print(f"Total content chars: {total_chars:,}")
    print(f"→ {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
