"""experiments/response_source.py
回答生成ソース — Codex MCP クライアント + フォールバックチェーン

役割分担:
  Claude  → 質問の品質を磨く (meta_generator.py)
  Codex   → 回答の品質を磨く (このモジュール)
  パイプライン → 審判

自作自演を避けるため、回答生成は Claude ではなく Codex が担当する。
Codex 不使用時は Anthropic SDK にフォールバックするが、
改善ループでは回答者としてのフィードバックを受けて回答を磨く。

フォールバック順:
1. Codex MCP (codex --mcp の stdio transport)
2. Anthropic SDK 直接呼び出し (回答者ロール)
3. 静的プレースホルダー (オフライン/CI 用)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from typing import List, Optional

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

RESPONDENT_IMPROVE_SYSTEM_PROMPT = """\
あなたはAIアシスタントです。前回の回答が意味監査で不十分と判定されました。
監査結果のフィードバックを踏まえて、回答の品質を改善してください。

改善の方針:
- miss した命題に対応する内容を具体的に追加する
- 安易な短絡や定型句を避け、質問の核心により深く踏み込む
- 構造的な問題（用語捏造、主題逸脱など）があれば修正する
"""

RESPONDENT_IMPROVE_USER_TEMPLATE = """\
## 質問
{question}

## 前回の回答
{previous_response}

## 監査結果
- verdict: {verdict}
- S (構造完全性): {S}
- C (命題被覆率): {C}
- ΔE (意味距離): {delta_e}
- hit した命題: {hit_props}
- miss した命題: {miss_props}

## 指示
miss した命題の内容をカバーし、verdict を改善する回答を作成してください。
回答テキストのみを返してください。
"""


async def _get_codex_response_async(prompt: str) -> Optional[str]:
    """Codex MCP サーバーから回答を取得する (async)"""
    if not _HAS_MCP_CLIENT or not _HAS_CODEX:
        return None
    try:
        params = StdioServerParameters(command="codex", args=["--mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("codex", {"prompt": prompt})
                if result.content:
                    return result.content[0].text
    except Exception:
        logger.exception("Codex MCP 呼び出し失敗")
    return None


def _call_codex(prompt: str) -> Optional[str]:
    """Codex MCP を同期的に呼び出す"""
    if not _HAS_MCP_CLIENT or not _HAS_CODEX:
        return None
    try:
        return asyncio.run(_get_codex_response_async(prompt))
    except Exception:
        logger.warning("Codex MCP フォールバック")
    return None


def _call_anthropic(
    system: str,
    user_content: str,
) -> Optional[str]:
    """Anthropic SDK で回答を生成する"""
    if not _HAS_ANTHROPIC:
        return None
    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        return message.content[0].text
    except Exception:
        logger.exception("Anthropic SDK 回答生成失敗")
    return None


def get_response(question: str, use_codex: bool = True) -> tuple[str, str]:
    """質問に対する初回回答を取得する

    Args:
        question: 質問テキスト
        use_codex: Codex MCP を試みるかどうか

    Returns:
        (response_text, source) のタプル
        source は "codex_mcp" | "anthropic_direct" | "placeholder"
    """
    # 1. Codex MCP
    if use_codex:
        result = _call_codex(question)
        if result:
            return result, "codex_mcp"

    # 2. Anthropic SDK (回答者ロール)
    result = _call_anthropic(RESPONDENT_SYSTEM_PROMPT, question)
    if result:
        return result, "anthropic_direct"

    # 3. プレースホルダー
    logger.warning("全ソース利用不可: プレースホルダーを返します")
    return "", "placeholder"


def improve_response(
    question: str,
    previous_response: str,
    audit_result: dict,
    core_propositions: List[str],
    use_codex: bool = True,
) -> tuple[str, str]:
    """監査結果を踏まえて回答を改善する

    Codex に前回の回答 + 監査フィードバックを渡し、改善された回答を得る。
    Codex 不使用時は Anthropic SDK にフォールバック。

    Args:
        question: 質問テキスト
        previous_response: 前回の回答テキスト
        audit_result: audit() の戻り値
        core_propositions: 現在の core_propositions リスト
        use_codex: Codex MCP を試みるかどうか

    Returns:
        (improved_response, source) のタプル
    """
    state = audit_result.get("state", {})
    evidence = audit_result.get("evidence", {})
    policy = audit_result.get("policy", {})

    hit_ids: List[int] = evidence.get("hit_ids", [])
    miss_ids: List[int] = evidence.get("miss_ids", [])
    hit_props = [core_propositions[i] for i in hit_ids if i < len(core_propositions)]
    miss_props = [core_propositions[i] for i in miss_ids if i < len(core_propositions)]

    feedback_prompt = RESPONDENT_IMPROVE_USER_TEMPLATE.format(
        question=question,
        previous_response=previous_response[:2000],
        verdict=policy.get("verdict", "unknown"),
        S=round(state.get("S", 0.0), 4),
        C=round(state["C"], 4) if state.get("C") is not None else "N/A",
        delta_e=round(state["delta_e"], 4) if state.get("delta_e") is not None else "N/A",
        hit_props=hit_props,
        miss_props=miss_props,
    )

    # 1. Codex MCP (改善プロンプト付き)
    if use_codex:
        codex_prompt = (
            f"以下の質問に対する前回の回答を改善してください。\n\n{feedback_prompt}"
        )
        result = _call_codex(codex_prompt)
        if result:
            return result, "codex_mcp"

    # 2. Anthropic SDK (改善ロール)
    result = _call_anthropic(RESPONDENT_IMPROVE_SYSTEM_PROMPT, feedback_prompt)
    if result:
        return result, "anthropic_direct"

    # 3. フォールバック: 前回の回答をそのまま返す
    logger.warning("回答改善不可: 前回の回答を返します")
    return previous_response, "fallback_unchanged"
