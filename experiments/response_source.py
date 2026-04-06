"""experiments/response_source.py
回答生成ソース — Codex MCP クライアント + フォールバックチェーン

フォールバック順:
1. Codex MCP (codex --mcp の stdio transport)
2. Anthropic SDK 直接呼び出し (回答者ロール)
3. 静的プレースホルダー (オフライン/CI 用)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Optional

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False

try:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client, StdioServerParameters
    _HAS_MCP_CLIENT = True
except ImportError:
    _HAS_MCP_CLIENT = False

logger = logging.getLogger(__name__)

# Codex バイナリの存在チェック
_HAS_CODEX = shutil.which("codex") is not None

RESPONDENT_SYSTEM_PROMPT = """\
あなたはAIアシスタントです。与えられた質問に対して、誠実かつ具体的に回答してください。
抽象的な逃げや安全性定型句は避け、質問の核心に直接答えてください。
"""


async def _get_codex_response_async(question: str) -> Optional[str]:
    """Codex MCP サーバーから回答を取得する (async)"""
    if not _HAS_MCP_CLIENT or not _HAS_CODEX:
        return None
    try:
        params = StdioServerParameters(command="codex", args=["--mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("codex", {"prompt": question})
                if result.content:
                    return result.content[0].text
    except Exception:
        logger.exception("Codex MCP 呼び出し失敗")
    return None


def _get_anthropic_response(question: str) -> Optional[str]:
    """Anthropic SDK で回答を生成する (回答者ロール)"""
    if not _HAS_ANTHROPIC:
        return None
    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=RESPONDENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": question}],
        )
        return message.content[0].text
    except Exception:
        logger.exception("Anthropic SDK 回答生成失敗")
    return None


def get_response(question: str, use_codex: bool = True) -> tuple[str, str]:
    """質問に対する回答を取得する

    Args:
        question: 質問テキスト
        use_codex: Codex MCP を試みるかどうか

    Returns:
        (response_text, source) のタプル
        source は "codex_mcp" | "anthropic_direct" | "placeholder"
    """
    # 1. Codex MCP
    if use_codex and _HAS_MCP_CLIENT and _HAS_CODEX:
        try:
            result = asyncio.run(_get_codex_response_async(question))
            if result:
                return result, "codex_mcp"
        except Exception:
            logger.warning("Codex MCP フォールバック")

    # 2. Anthropic SDK
    result = _get_anthropic_response(question)
    if result:
        return result, "anthropic_direct"

    # 3. プレースホルダー
    logger.warning("全ソース利用不可: プレースホルダーを返します")
    return "", "placeholder"
