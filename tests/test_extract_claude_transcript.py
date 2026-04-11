"""tests/test_extract_claude_transcript.py — Claude Code jsonl extractor tests"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "analysis"))

import extract_claude_transcript as ect  # noqa: E402


def test_user_string_content():
    """content が str の user レコード → そのまま返す"""
    rec = {"type": "user", "message": {"role": "user", "content": "hello"}}
    assert ect._is_real_user_input(rec) == "hello"


def test_user_list_text_only():
    """content が text ブロックだけの list → text を連結"""
    rec = {
        "type": "user",
        "message": {"content": [{"type": "text", "text": "質問です"}]},
    }
    assert ect._is_real_user_input(rec) == "質問です"


def test_user_list_tool_result_only_returns_none():
    """tool_result だけの user レコード → None (ツール出力)"""
    rec = {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "content": "..."}]},
    }
    assert ect._is_real_user_input(rec) is None


def test_user_list_mixed_text_and_tool_result_preserves_text():
    """text + tool_result 混在 → text を拾う (Codex PR #61 r3067358382 回帰)

    旧実装: tool_result を見た時点で None を返し、text ブロックを drop する。
    → その user turn が消え、周辺の assistant chunks が隣接 turn に
      misassign される。
    修正後: tool_result は無視して text ブロックだけ拾う。
    """
    rec = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": "tool output..."},
                {"type": "text", "text": "続きの質問"},
            ]
        },
    }
    assert ect._is_real_user_input(rec) == "続きの質問"


def test_user_list_tool_result_then_text_blocks():
    """tool_result が先に来ても後続の text を拾う (順序独立)"""
    rec = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": "..."},
                {"type": "text", "text": "A"},
                {"type": "tool_result", "content": "..."},
                {"type": "text", "text": "B"},
            ]
        },
    }
    result = ect._is_real_user_input(rec)
    assert result == "A\nB"


def test_assistant_text_blocks_only():
    """assistant レコードから text ブロックを連結"""
    rec = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "回答の一部"},
                {"type": "tool_use", "id": "t1", "name": "Bash"},
                {"type": "text", "text": "続き"},
            ]
        },
    }
    assert ect._extract_assistant_text(rec) == "回答の一部\n続き"


def test_assistant_thinking_blocks_excluded():
    """thinking ブロックは除外される"""
    rec = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "考え中..."},
                {"type": "text", "text": "答え"},
            ]
        },
    }
    assert ect._extract_assistant_text(rec) == "答え"


def test_assistant_tool_use_only_returns_empty():
    """text ブロックが無い assistant レコードは空文字"""
    rec = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash"}]},
    }
    assert ect._extract_assistant_text(rec) == ""


def test_extract_transcript_mixed_block_not_dropped(tmp_path):
    """混在 block の user turn が extract_transcript で失われないことを検証"""
    jsonl_path = tmp_path / "test_session.jsonl"
    records = [
        {"type": "user", "message": {"content": "最初の質問"}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "回答1"}]},
        },
        # tool_result + text の混在: 旧実装ではこの user turn が drop される
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "content": "..."},
                    {"type": "text", "text": "追加の質問"},
                ]
            },
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "回答2"}]},
        },
    ]
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    turns = ect.extract_transcript(jsonl_path)

    # user turn 2 つ + assistant turn 2 つが正しく segment されているはず
    user_turns = [t for t in turns if t["role"] == "user"]
    assistant_turns = [t for t in turns if t["role"] == "assistant"]

    assert len(user_turns) == 2
    assert user_turns[0]["content"] == "最初の質問"
    assert user_turns[1]["content"] == "追加の質問"
    assert len(assistant_turns) == 2
    assert assistant_turns[0]["content"] == "回答1"
    assert assistant_turns[1]["content"] == "回答2"


def test_extract_transcript_skips_pure_tool_result_user_records(tmp_path):
    """tool_result のみの user レコードはスキップされ、隣接 assistant turn の
    segmentation を壊さない。
    """
    jsonl_path = tmp_path / "test_session.jsonl"
    records = [
        {"type": "user", "message": {"content": "質問"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "回答前半"},
                    {"type": "tool_use", "id": "t1", "name": "Bash"},
                ]
            },
        },
        # tool_result のみ (ツール結果が返ってきたケース) → スキップ
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "content": "ls output"}]},
        },
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "回答後半"}]},
        },
    ]
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    turns = ect.extract_transcript(jsonl_path)

    user_turns = [t for t in turns if t["role"] == "user"]
    assistant_turns = [t for t in turns if t["role"] == "assistant"]

    # tool_result の user レコードは消費されないので user turn は 1 つ
    assert len(user_turns) == 1
    assert user_turns[0]["content"] == "質問"
    # assistant の前半と後半は 1 つの turn に merge される (間に実ユーザー
    # 入力が無いので、同じ turn の継続扱い)
    assert len(assistant_turns) == 1
    assert "回答前半" in assistant_turns[0]["content"]
    assert "回答後半" in assistant_turns[0]["content"]
