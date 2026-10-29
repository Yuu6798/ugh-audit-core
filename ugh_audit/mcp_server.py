"""
ugh_audit/mcp_server.py
MCP サーバー — 招待制ベータ（パイプライン A 対応）

Model Context Protocol (MCP) で audit_answer ツールを公開する。
Bearerヘッダーを設定できるクライアントで /mcp/ に接続する。OAuthは未実装。

起動方法:
    python -m ugh_audit.mcp_server                 # Streamable HTTP (port 8000)
    python -m ugh_audit.mcp_server --port 9000      # ポート指定
"""
from __future__ import annotations

import json as _json
import logging
import math
import os
import sys
from dataclasses import dataclass, field
from functools import partial, wraps

import anyio
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import dependencies as _deps
from .beta_access import audit_access, require_principal, validate_payload

if TYPE_CHECKING:
    from .reference.golden_store import GoldenStore
    from .storage.audit_db import AuditDB

# パイプライン A の共有基盤 (server.py と同一ロジックを参照)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ugh_calculator import (  # noqa: E402
    META_SOURCE_LLM,
)

from . import pipeline as _pipeline  # noqa: E402
from .semantic_matching import DECISION_SCOPE, METRICS_SCOPE  # noqa: E402

# 後方互換: test_pipeline_a が直接 import する
from .pipeline import (
    SCHEMA_VERSION,  # noqa: E402
    _gate_verdict_safe,  # noqa: E402, F401
    _primary_fail,  # noqa: E402, F401
    _primary_fail_safe,  # noqa: E402, F401
)

# detector (検出層) — 利用可能な場合のみ使用。
# _HAS_DETECTOR / _detect は test の monkeypatch 互換のため module level に残す。
try:
    from detector import detect as _detect  # noqa: E402
    _HAS_DETECTOR = True
except ImportError:
    _HAS_DETECTOR = False
    _detect = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _make_semantic_judge():
    """Use server configuration only, never an assessment supplied by a caller."""
    from .semantic_matching import make_judge_from_env

    return make_judge_from_env()


def _valid_remote_metrics(result: dict) -> bool:
    """Require the current scoped, nullable public contract before using its proof."""
    if (not isinstance(result, dict) or result.get("schema_version") != "4.0.0"
            or result.get("decision_scope") != DECISION_SCOPE
            or result.get("metrics_scope") != METRICS_SCOPE):
        return False
    if result.get("mode") not in ("computed", "computed_ai_draft", "degraded"):
        return False
    if result.get("metadata_source") not in ("inline", "llm_generated", "none", "fallback"):
        return False
    if result.get("verdict") not in ("accept", "rewrite", "degraded"):
        return False
    if not isinstance(result.get("is_reliable"), bool):
        return False
    if result["is_reliable"] is (result["mode"] == "degraded"):
        return False
    if any(key not in result or result[key] is not None for key in ("S", "delta_e", "quality_score")):
        return False
    if "C" not in result:
        return False
    coverage = result["C"]
    if coverage is not None and (type(coverage) not in (int, float)
            or not 0 <= coverage <= 1 or not math.isfinite(coverage)):
        return False
    gate = result.get("structural_gate")
    if not isinstance(gate, dict) or gate.get("binding") is not False:
        return False
    for key in ("f1", "f2", "f3", "f4"):
        value = gate.get(key)
        if value is not None and (type(value) not in (int, float)
                or not 0 <= value <= 1 or not math.isfinite(value)):
            return False
    legacy = result.get("legacy_metrics")
    if not isinstance(legacy, dict):
        return False
    for name, low, high in (("S", 0, 1), ("C", 0, 1), ("delta_e", 0, 1), ("quality_score", 1, 5)):
        value = legacy.get(name)
        if value is not None and (type(value) not in (int, float)
                or not low <= value <= high or not math.isfinite(value)):
            return False
    from .semantic_matching import validate_public_summary

    return validate_public_summary(result)


def _read_remote_json(resp):
    """Reject HTTP failures, oversized bodies, nonstandard JSON and duplicate keys."""
    if getattr(resp, "status", None) != 200:
        raise ValueError("remote_http_status")
    raw = resp.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("remote_body_limit")

    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value

    def reject_constant(value):
        raise ValueError("nonstandard_json_constant")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite_json_number")
        return number

    return _json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                       parse_constant=reject_constant, parse_float=finite_float)


# ---------------------------------------------------------------------------
# MCP サーバー定義
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "UGH Audit",
    instructions=(
        "AI回答の意味論的監査ツール。"
        "明示された要件ごとの充足・欠落・判定保留を根拠付きで照合する。"
        "C は要件充足率、従来指標と GRV は参考診断。"
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
    S: Optional[float]
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
    decision_scope: str = DECISION_SCOPE
    metrics_scope: str = METRICS_SCOPE
    assessment_context: Optional[Dict] = None
    semantic_matching: Optional[Dict] = None
    requirement_verdict: str = "undetermined"
    requires_review: bool = True
    legacy_metrics: Optional[Dict] = None
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


def _remote_open(req, timeout):
    """Forward this caller's identity only over TLS, without following redirects."""
    import urllib.request
    from urllib.parse import urlsplit

    principal = require_principal()
    token = principal.token or os.environ.get("UGH_REMOTE_TOKEN")
    if not token:
        return urllib.request.urlopen(req, timeout=timeout)
    parsed = urlsplit(req.full_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Authenticated upstream requires HTTPS")
    req.add_header("Authorization", "Bearer " + token)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    return urllib.request.build_opener(NoRedirect()).open(req, timeout=timeout)


def _proxy_audit(remote_api: str, **kwargs) -> AuditOutput:
    """リモート API に監査リクエストを転送し、結果を AuditOutput に変換する"""
    import urllib.request

    url = remote_api.rstrip("/") + "/api/audit"
    payload = {k: v for k, v in kwargs.items() if v is not None}
    data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _remote_open(req, timeout=60) as resp:
            result = _read_remote_json(resp)
    except Exception as exc:
        # Error bodies and exception messages can echo request contents.
        result = {
            "schema_version": SCHEMA_VERSION, "S": None, "C": None,
            "delta_e": None, "quality_score": None, "verdict": "degraded",
            "mode": "degraded", "is_reliable": False, "structural_gate": {},
            "verdict_advisory": "degraded",
            "errors": [f"remote_api_error:{type(exc).__name__}"],
            "degraded_reason": ["remote_api_error"],
        }
    if not isinstance(result, dict):
        result = {
            "schema_version": SCHEMA_VERSION, "S": None, "verdict": "degraded",
            "mode": "degraded", "is_reliable": False,
            "errors": ["remote_api_invalid_response"],
        }
    eligible = _valid_remote_metrics(result)
    if not eligible:
        legacy = {
            key: value if type(value) in (int, float) and 0 <= value <= 5 and math.isfinite(value) else None
            for key in ("S", "C", "delta_e", "quality_score")
            for value in (result.get(key),)
        }
        legacy["verdict"] = result.get("verdict") if isinstance(result.get("verdict"), str) else None
        result = {
            "schema_version": SCHEMA_VERSION, "S": None, "C": None,
            "delta_e": None, "quality_score": None, "verdict": "degraded",
            "mode": "degraded", "is_reliable": False, "structural_gate": {},
            "legacy_metrics": legacy, "errors": ["remote_api_invalid_metrics"],
        }

    from .semantic_matching import apply_semantic_gate

    context = kwargs.get("question_meta")
    if context is None:
        context = result.get("assessment_context")
        eligible = (eligible and kwargs.get("auto_generate_meta") is True
                    and result.get("metadata_source") == "llm_generated")
    eligible = eligible and isinstance(context, dict) and bool(context)
    result = apply_semantic_gate(
        result,
        result.get("semantic_matching"),
        response=kwargs.get("response"),
        question_meta=context,
        eligible=eligible, ineligible_reason="remote_contract_or_transport_unverified",
    )
    gate = result.get("structural_gate") or {}
    primary_verdict = result.get("verdict", "degraded")
    return AuditOutput(
        schema_version=SCHEMA_VERSION,
        S=result.get("S"),
        decision_scope=result["decision_scope"],
        metrics_scope=result["metrics_scope"],
        assessment_context=context if isinstance(context, dict) else None,
        C=result.get("C"),
        delta_e=result.get("delta_e"),
        quality_score=result.get("quality_score"),
        verdict=primary_verdict,
        hit_rate=result.get("hit_rate"),
        structural_gate=gate,
        saved_id=result.get("saved_id") if result.get("mode") != "degraded" else None,
        mode=result.get("mode", "degraded"),
        is_reliable=result.get("is_reliable", False),
        matched_id=result.get("matched_id"),
        metadata_source=result.get("metadata_source", "none"),
        semantic_matching=result.get("semantic_matching"),
        requirement_verdict=result.get("requirement_verdict", "undetermined"),
        requires_review=result.get("requires_review", True),
        legacy_metrics=result.get("legacy_metrics"),
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


def _worker_tool(fn):
    # FastMCP otherwise executes synchronous tools on its event-loop thread.
    # AnyIO shields the actual worker on cancellation, retaining its audit slot.
    @wraps(fn)
    async def worker(**kwargs):
        return await anyio.to_thread.run_sync(partial(fn, **kwargs))
    mcp.tool()(worker)
    return fn


@_worker_tool
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
    要件ごとの照合結果、要件充足率 C、verdict（判定）を返す。
    未校正の S・ΔE・quality_score は null、従来値は legacy_metrics に格納する。
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
    require_principal()
    validate_payload(question, response, reference, session_id, question_meta)
    # プロキシモード: UGH_REMOTE_API が設定されている場合、リモート API に転送
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        with audit_access(_get_db()):
            return _proxy_audit(
                remote_api, question=question, response=response,
                reference=reference, session_id=session_id,
                question_meta=question_meta,
                auto_generate_meta=auto_generate_meta,
                retry_of=retry_of,
            )

    db = _get_db()
    with audit_access(db, retry_of) as principal:
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
            semantic_judge=_make_semantic_judge(),
        )

        # degraded 時は DB に保存しない (未計算ログでベースラインを汚染させない)
        saved_id: Optional[int] = None
        if result["mode"] in ("computed", "computed_ai_draft"):
            gate = result["structural_gate"]
            saved_id = db.save(
                owner_id=principal.owner_id,
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
                f4=gate["f4"],
                hit_rate=result["hit_rate"] or "",
                metadata_source=result["metadata_source"],
                generated_meta=_json.dumps(
                    result.get("_question_meta") or {}, ensure_ascii=False,
                ) if result["metadata_source"] == META_SOURCE_LLM else "",
                hit_sources=_json.dumps(result.get("hit_sources"), ensure_ascii=False),
                retry_of=retry_of,
                semantic_matching=result["semantic_matching"],
                requirement_verdict=result["requirement_verdict"],
                requires_review=result["requires_review"],
                legacy_metrics=result["legacy_metrics"],
                schema_version=result["schema_version"],
                decision_scope=result["decision_scope"],
                metrics_scope=result["metrics_scope"],
                question_meta=result.get("_question_meta"),
                structural_gate=result.get("structural_gate"),
                mode=result["mode"],
                is_reliable=result["is_reliable"],
            )

        return AuditOutput(
            schema_version=result["schema_version"],
            decision_scope=result["decision_scope"],
            metrics_scope=result["metrics_scope"],
            assessment_context=result.get("_question_meta"),
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
            semantic_matching=result["semantic_matching"],
            requirement_verdict=result["requirement_verdict"],
            requires_review=result["requires_review"],
            legacy_metrics=result["legacy_metrics"],
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
        with _remote_open(req, timeout=30) as resp:
            result = _read_remote_json(resp)
    except urllib.error.HTTPError as e:
        return {"error": f"remote_api_error:{e.code}"}
    except Exception as e:
        return {"error": f"remote_api_error:{type(e).__name__}"}
    from .storage.audit_db import guard_history_row

    def guard_remote_row(row):
        if not isinstance(row, dict):
            return {"error": "remote_history_invalid"}
        if not _valid_remote_metrics(row):
            # An inconsistent remote envelope cannot become eligible when the
            # projected row is guarded again by another history consumer.
            row = {**row, "schema_version": "", "source_schema_version": ""}
        return guard_history_row(row)

    if path.startswith("/api/session/"):
        if not isinstance(result, dict):
            return {"error": "remote_session_invalid"}
        counts = [result.get(name) for name in ("total", "verified_total", "unverified_total")]
        if (result.get("schema_version") == "4.0.0"
                and result.get("decision_scope") == DECISION_SCOPE
                and result.get("metrics_scope") == METRICS_SCOPE
                and all(type(value) is int and value >= 0 for value in counts)
                and counts[0] == counts[1] + counts[2]
                and all(name in result and result[name] is None for name in (
                    "avg_S", "avg_delta_e", "min_delta_e", "max_delta_e", "avg_quality_score",
                ))):
            return result
        total = result.get("total")
        total = total if type(total) is int and total >= 0 else 0
        return {
            "session_id": result.get("session_id", ""),
            "schema_version": "4.0.0", "decision_scope": DECISION_SCOPE,
            "metrics_scope": METRICS_SCOPE,
            "total": total, "verified_total": 0, "unverified_total": total,
            "avg_S": None,
            "avg_delta_e": None, "min_delta_e": None, "max_delta_e": None,
            "avg_quality_score": None, "legacy_summary": result,
        }
    if isinstance(result, list):
        return [guard_remote_row(row) for row in result]
    if isinstance(result, dict) and "error" not in result:
        return guard_remote_row(result)
    return result


@_worker_tool
def get_audit(audit_id: int) -> Dict:
    """ID指定で監査結果を1件取得する。

    Args:
        audit_id: 監査結果のID
    """
    principal = require_principal()
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/audit/{audit_id}")
    db = _get_db()
    row = db.get_by_id(audit_id, owner_id=principal.owner_id)
    if row is None:
        return {"error": f"audit_id {audit_id} not found"}
    return row


@_worker_tool
def get_history(limit: int = 20) -> List[Dict]:
    """直近の監査履歴を取得する。

    Args:
        limit: 取得件数 (デフォルト: 20)
    """
    limit = max(1, min(limit, 500))
    principal = require_principal()
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/history?limit={limit}")
    db = _get_db()
    return db.list_recent(limit=limit, owner_id=principal.owner_id)


@_worker_tool
def get_session_summary(session_id: str) -> Dict:
    """セッション単位の集計サマリーを取得する。

    Args:
        session_id: セッションID
    """
    principal = require_principal()
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        from urllib.parse import quote
        return _proxy_get(remote_api, f"/api/session/{quote(session_id, safe='')}")
    db = _get_db()
    return db.session_summary(session_id, owner_id=principal.owner_id)


@_worker_tool
def get_drift_history(limit: int = 100) -> List[Dict]:
    """要件充足率と判定の時系列を取得する。従来指標は参考診断。

    Args:
        limit: 取得件数 (デフォルト: 100)
    """
    limit = max(1, min(limit, 1000))
    principal = require_principal()
    remote_api = os.environ.get("UGH_REMOTE_API")
    if remote_api:
        return _proxy_get(remote_api, f"/api/drift?limit={limit}")
    db = _get_db()
    return db.drift_history(limit=limit, owner_id=principal.owner_id)


# ---------------------------------------------------------------------------
# スタンドアロン起動
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="UGH Audit MCP Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()

    import uvicorn

    # Use the same authenticated ASGI boundary as the REST entry point.
    uvicorn.run("ugh_audit.server:app", host=args.host, port=args.port)
