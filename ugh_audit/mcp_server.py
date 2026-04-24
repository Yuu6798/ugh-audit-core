"""
ugh_audit/mcp_server.py
MCP サーバー — ChatGPT Connectors 用（パイプライン A 対応）

Model Context Protocol (MCP) で audit_answer ツールを公開する。
ChatGPT Settings > Connectors から MCP URL を登録して利用する。

起動方法:
    python -m ugh_audit.mcp_server                 # Streamable HTTP (port 8000)
    python -m ugh_audit.mcp_server --port 9000      # ポート指定
"""
from __future__ import annotations

import json as _json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from . import dependencies as _deps

if TYPE_CHECKING:
    from .reference.golden_store import GoldenStore
    from .storage.audit_db import AuditDB

# パイプライン A の共有基盤 (server.py と同一ロジックを参照)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import (  # noqa: E402
    META_SOURCE_LLM,
)

from . import pipeline as _pipeline  # noqa: E402
from .pipeline import SCHEMA_VERSION  # noqa: E402

# 後方互換: test_pipeline_a が直接 import する
from .pipeline import _gate_verdict_safe  # noqa: E402, F401
from .pipeline import _primary_fail  # noqa: E402, F401
from .pipeline import _primary_fail_safe  # noqa: E402, F401

# detector (検出層) — 利用可能な場合のみ使用。
# _HAS_DETECTOR / _detect は test の monkeypatch 互換のため module level に残す。
try:
    from detector import detect as _detect  # noqa: E402
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False
    _detect = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MCP サーバー定義
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "UGH Audit",
    instructions=(
        "AI回答の意味論的監査ツール。"
        "パイプライン A (S / C / ΔE / quality_score) で意味的誠実性を定量評価する。"
    ),
    streamable_http_path="/",
    stateless_http=True,
)

# ---------------------------------------------------------------------------
# 共有インスタンス（遅延初期化）
# ---------------------------------------------------------------------------

def _get_db() -> AuditDB:
    return _deps.get_db()


def _get_golden() -> GoldenStore:
    return _deps.get_golden()


def configure(
    db: Optional[AuditDB] = None,
    golden: Optional[GoldenStore] = None,
) -> None:
    """テストやカスタム設定用にグローバルインスタンスを差し替える"""
    if db is None and golden is None:
        _deps.reset()
        return
    _deps.configure(db=db, golden=golden)


# ---------------------------------------------------------------------------
# 出力データクラス
# ---------------------------------------------------------------------------


@dataclass
class AuditOutput:
    """audit_answer ツールの構造化出力"""

    schema_version: str
    S: float
    C: Optional[float]
    delta_e: Optional[float]
    quality_score: Optional[float]
    verdict: str
    hit_rate: Optional[str]
    structural_gate: Dict
    saved_id: Optional[int]
    mode: str
    is_reliable: bool
    matched_id: Optional[str]
    metadata_source: str
    # Phase E: advisory verdict は常時必須 (primary verdict にフォールバック)。
    # dataclass の順序要件上 default を持つフィールドの直前に配置する。
    verdict_advisory: str = "degraded"
    computed_components: List[str] = field(default_factory=list)
    missing_components: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    degraded_reason: List[str] = field(default_factory=list)
    mode_affordance: Optional[Dict] = None
    soft_rescue: Optional[Dict] = None
    grv: Optional[Dict] = None
    response_mode_signal: Optional[Dict] = None
    mode_conditioned_grv: Optional[Dict] = None
    advisory_flags: List[str] = field(default_factory=list)
    hit_sources: Optional[Dict] = None  # paper defense: core vs cascade 分離サマリ


# ---------------------------------------------------------------------------
# ツール定義
# ---------------------------------------------------------------------------


def _proxy_audit(remote_api: str, **kwargs) -> AuditOutput:
    """リモート API に監査リクエストを転送し、結果を AuditOutput に変換する"""
    import urllib.request
    import urllib.error

    url = remote_api.rstrip("/") + "/api/audit"
    payload = {k: v for k, v in kwargs.items() if v is not None}
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return AuditOutput(
            schema_version=SCHEMA_VERSION, S=0.0, C=None, delta_e=None,
            quality_score=None, verdict="degraded", hit_rate=None,
            structural_gate={}, saved_id=None, mode="degraded",
            is_reliable=False, matched_id=None, metadata_source="none",
            verdict_advisory="degraded",
            errors=[f"remote_api_error: {e.code} {body[:200]}"],
            degraded_reason=["remote_api_error"],
        )
    except Exception as e:
        return AuditOutput(
            schema_version=SCHEMA_VERSION, S=0.0, C=None, delta_e=None,
            quality_score=None, verdict="degraded", hit_rate=None,
            structural_gate={}, saved_id=None, mode="degraded",
            is_reliable=False, matched_id=None, metadata_source="none",
            verdict_advisory="degraded",
            errors=[f"remote_api_error: {e}"],
            degraded_reason=["remote_api_error"],
        )

    gate = result.get("structural_gate") or {}
    primary_verdict = result.get("verdict", "degraded")
    return AuditOutput(
        schema_version=result.get("schema_version", "2.0.0"),
        S=result.get("S", 0.0),
        C=result.get("C"),
        delta_e=result.get("delta_e"),
        quality_score=result.get("quality_score"),
        verdict=primary_verdict,
        hit_rate=result.get("hit_rate"),
        structural_gate=gate,
        saved_id=result.get("saved_id"),
        mode=result.get("mode", "degraded"),
        is_reliable=result.get("is_reliable", False),
        matched_id=result.get("matched_id"),
        metadata_source=result.get("metadata_source", "none"),
        verdict_advisory=result.get("verdict_advisory", primary_verdict),
        computed_components=result.get("computed_components", []),
        missing_components=result.get("missing_components", []),
        errors=result.get("errors", []),
        degraded_reason=result.get("degraded_reason", []),
        mode_affordance=result.get("mode_affordance"),
        soft_rescue=result.get("soft_rescue"),
        grv=result.get("grv"),
        response_mode_signal=result.get("response_mode_signal"),
        mode_conditioned_grv=result.get("mode_conditioned_grv"),
        advisory_flags=result.get("advisory_flags", []),
        hit_sources=result.get("hit_sources"),
    )


@mcp.tool()
def audit_answer(
    question: str,
    response: str,
    reference: Optional[str] = None,
    session_id: Optional[str] = None,
    question_meta: Optional[Dict] = None,
    auto_generate_meta: bool = False,
    retry_of: Optional[int] = None,
) -> AuditOutput:
    """AI回答を意味監査する。

    question（質問）、response（AI回答）、reference（期待回答、省略可）を受け取り、
    PoR（S, C）、ΔE（意味ズレ量）、quality_score（品質スコア）、verdict（判定）を返す。
    結果はDBに自動保存される。

    question_meta が未提供の場合、auto_generate_meta=True なら LLM で動的生成を試みる。
    それでも生成できない場合は verdict="degraded" を返す。

    Args:
        question: ユーザーの質問
        response: AIの回答
        reference: 期待される正解（省略時は GoldenStore から自動検索）
        session_id: セッションID（省略時は自動生成）
        question_meta: 問題メタデータ（core_propositions 等を含む dict）
        auto_generate_meta: question_meta 未提供時に LLM で動的生成する (opt-in)
    """
    # プロキシモード: UGH_REMOTE_API が設定されている場合、リモート API に転送
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_audit(
            remote_api, question=question, response=response,
            reference=reference, session_id=session_id,
            question_meta=question_meta,
            auto_generate_meta=auto_generate_meta,
            retry_of=retry_of,
        )

    db = _get_db()
    golden = _get_golden()
    ref = reference or golden.find_reference(question)

    result = _pipeline.run_audit(
        question=question,
        response=response,
        reference=ref,
        question_meta=question_meta,
        session_id=session_id,
        auto_generate_meta=auto_generate_meta,
        detect_fn=_detect if _HAS_DETECTOR else None,
    )

    # degraded 時は DB に保存しない (未計算ログでベースラインを汚染させない)
    saved_id: Optional[int] = None
    if result["mode"] in ("computed", "computed_ai_draft"):
        gate = result["structural_gate"]
        saved_id = db.save(
            session_id=result["_session_id"],
            question=result["_question"],
            response=result["_response"],
            reference=result["_reference"],
            S=result["S"],
            C=result["C"],
            delta_e=result["delta_e"],
            quality_score=result["quality_score"],
            verdict=result["verdict"],
            f1=gate["f1"],
            f2=gate["f2"],
            f3=gate["f3"],
            f4=gate["f4"] if gate["f4"] is not None else 0.0,
            hit_rate=result["hit_rate"] or "",
            metadata_source=result["metadata_source"],
            generated_meta=_json.dumps(
                result.get("_question_meta") or {}, ensure_ascii=False,
            ) if result["metadata_source"] == META_SOURCE_LLM else "",
            hit_sources=_json.dumps(
                result.get("_hit_sources", {}), ensure_ascii=False,
            ),
            retry_of=retry_of,
        )

    return AuditOutput(
        schema_version=result["schema_version"],
        S=result["S"],
        C=result["C"],
        delta_e=result["delta_e"],
        quality_score=result["quality_score"],
        verdict=result["verdict"],
        hit_rate=result["hit_rate"],
        structural_gate=result["structural_gate"],
        saved_id=saved_id,
        mode=result["mode"],
        is_reliable=result["is_reliable"],
        matched_id=result["matched_id"],
        metadata_source=result["metadata_source"],
        verdict_advisory=result["verdict_advisory"],
        computed_components=result["computed_components"],
        missing_components=result["missing_components"],
        errors=result["errors"],
        degraded_reason=result["degraded_reason"],
        mode_affordance=result["mode_affordance"],
        soft_rescue=result.get("soft_rescue"),
        grv=result.get("grv"),
        response_mode_signal=result.get("response_mode_signal"),
        mode_conditioned_grv=result.get("mode_conditioned_grv"),
        advisory_flags=list(result.get("advisory_flags", [])),
        hit_sources=result.get("hit_sources"),
    )


def _proxy_get(remote_api: str, path: str) -> Dict:
    """リモート API から GET リクエストで取得"""
    import urllib.error
    import urllib.request
    url = remote_api.rstrip("/") + path
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"error": f"remote_api_error: {e.code}", "detail": body[:200]}
    except Exception as e:
        return {"error": f"remote_api_error: {e}"}


@mcp.tool()
def get_audit(audit_id: int) -> Dict:
    """ID指定で監査結果を1件取得する。

    Args:
        audit_id: 監査結果のID
    """
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/audit/{audit_id}")
    db = _get_db()
    row = db.get_by_id(audit_id)
    if row is None:
        return {"error": f"audit_id {audit_id} not found"}
    return row


@mcp.tool()
def get_history(limit: int = 20) -> List[Dict]:
    """直近の監査履歴を取得する。

    Args:
        limit: 取得件数 (デフォルト: 20)
    """
    limit = max(1, min(limit, 500))
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/history?limit={limit}")
    db = _get_db()
    return db.list_recent(limit=limit)


@mcp.tool()
def get_session_summary(session_id: str) -> Dict:
    """セッション単位の集計サマリーを取得する。

    Args:
        session_id: セッションID
    """
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        from urllib.parse import quote
        return _proxy_get(remote_api, f"/api/session/{quote(session_id, safe='')}")
    db = _get_db()
    return db.session_summary(session_id)


@mcp.tool()
def get_drift_history(limit: int = 100) -> List[Dict]:
    """ΔE時系列データを取得する。品質推移の分析に使用。

    Args:
        limit: 取得件数 (デフォルト: 100)
    """
    limit = max(1, min(limit, 1000))
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/drift?limit={limit}")
    db = _get_db()
    return db.drift_history(limit=limit)


# ---------------------------------------------------------------------------
# スタンドアロン起動
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="UGH Audit MCP Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    args = parser.parse_args()

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.streamable_http_path = "/mcp"

    mcp.run(transport="streamable-http")
