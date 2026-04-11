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
from typing import Any, Dict, List, Optional, Tuple


# user record の分類
# - "real_text":    実ユーザー入力のテキストが取れた (turn 境界 + content)
# - "real_no_text": 実ユーザー入力だが text ブロックがない (image/document のみ)
#                   → turn 境界は保持するが content は placeholder
# - "tool_only":    tool_result のみ (ツール出力、turn 境界ではない)
# - "invalid":      malformed record
_USER_REAL_TEXT = "real_text"
_USER_REAL_NO_TEXT = "real_no_text"
_USER_TOOL_ONLY = "tool_only"
_USER_INVALID = "invalid"


def _classify_user_record(rec: Dict) -> Tuple[str, Optional[str]]:
    """user レコードを分類し (kind, text) を返す。

    text/image/document/tool_result が混在しうる Claude API の content 形式で、
    実ユーザー turn 境界 (text があってもなくても) と tool 出力だけの record
    を区別する。これによって text 無しの user turn (image 等) が境界を
    落とさず、assistant chunks が誤って merge されるのを防ぐ
    (Codex review PR #61 r3067358382 + r3067402451)。

    Returns:
        ("real_text", content)   — 実ユーザー入力、text あり
        ("real_no_text", None)   — 実ユーザー入力だが text ブロック無し
        ("tool_only", None)      — tool_result のみ (turn 境界ではない)
        ("invalid", None)        — malformed
    """
    if rec.get("type") != "user":
        return (_USER_INVALID, None)
    msg = rec.get("message", {})
    if not isinstance(msg, dict):
        return (_USER_INVALID, None)
    content = msg.get("content")
    if isinstance(content, str):
        return (_USER_REAL_TEXT, content)
    if not isinstance(content, list):
        return (_USER_INVALID, None)

    texts: List[str] = []
    has_tool_result = False
    has_other_non_text = False
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            texts.append(block.get("text", ""))
        elif btype == "tool_result":
            has_tool_result = True
        else:
            # image, document, etc — text を持たない実ユーザー入力
            has_other_non_text = True

    if texts:
        combined = "\n".join(t for t in texts if t.strip())
        if combined.strip():
            return (_USER_REAL_TEXT, combined)
    if has_other_non_text:
        return (_USER_REAL_NO_TEXT, None)
    if has_tool_result:
        return (_USER_TOOL_ONLY, None)
    return (_USER_INVALID, None)


def _is_real_user_input(rec: Dict) -> Optional[str]:
    """後方互換 wrapper: real_text の text を返し、それ以外は None。

    既存テストとの互換のため残す。新しいコードは `_classify_user_record`
    を直接使って kind と text を両方受け取ること (turn 境界の判定に必要)。
    """
    kind, text = _classify_user_record(rec)
    if kind == _USER_REAL_TEXT:
        return text
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
                kind, user_text = _classify_user_record(rec)
                if kind in (_USER_TOOL_ONLY, _USER_INVALID):
                    # tool output や malformed はスキップ (turn 境界ではない)
                    continue
                # real_text / real_no_text のどちらも turn 境界として扱う。
                # 直前の assistant chunks を flush してから user turn を追加する。
                _flush_assistant()
                turn_counter += 1
                if kind == _USER_REAL_TEXT:
                    content_str = user_text or ""
                else:
                    # 画像 / document only user turn は placeholder を置いて
                    # 境界だけ保持 (metric は assistant turn しか見ないので
                    # content は audit に影響しない)
                    content_str = "[non-text user content]"
                turns.append({
                    "turn": turn_counter,
                    "role": "user",
                    "content": content_str,
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
