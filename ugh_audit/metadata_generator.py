"""
ugh_audit/metadata_generator.py
メタデータ生成要求の共通フォーマッタ。
"""
from __future__ import annotations

from typing import Any, Optional

METADATA_GENERATION_SCHEMA_VERSION = "1.0.0"


def detect_missing_metadata(question_meta: Optional[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    meta = question_meta or {}
    if not meta.get("core_propositions"):
        missing.append("core_propositions")
    # trap_type="" は「罠なし」の明示指定 — 欠損とみなさない
    # trap_type が未設定 or None の場合のみ欠損
    if "trap_type" not in meta or meta["trap_type"] is None:
        missing.append("trap_type")
    return missing


def default_output_template() -> dict[str, Any]:
    return {
        "question": "",
        "core_propositions": [
            "回答で満たすべき核心命題を 1 文ずつ列挙",
        ],
        "trap_type": "binary_reduction | premise_acceptance | (空文字列=罠なし)",
        "disqualifying_shortcuts": [],
        "acceptable_variants": [],
        "metadata_confidence": 0.0,
        "rationale": "なぜこの命題群と trap_type を選んだか",
    }


def build_metadata_request(
    question: str,
    missing_fields: list[str],
    *,
    question_id: Optional[str] = None,
    metadata_source: str = "none",
) -> Optional[dict[str, Any]]:
    if not missing_fields:
        return None

    instructions = [
        "あなたは監査用メタデータ生成器です。",
        "入力質問に対して、監査に必要な最小メタデータだけを JSON で返してください。",
        "core_propositions は短く独立した命題として 2〜4 個に抑えてください。",
        "trap_type は binary_reduction, premise_acceptance, 空文字列(\"\") のいずれかを優先してください。",
        "不明な場合は推測しすぎず、metadata_confidence を下げてください。",
        "JSON 以外の文章は返さないでください。",
    ]

    return {
        "schema_version": METADATA_GENERATION_SCHEMA_VERSION,
        "generation_policy": "ai_draft",
        "question_id": question_id,
        "metadata_source": metadata_source,
        "required_fields": missing_fields,
        "instructions": instructions,
        "input": {
            "question": question,
        },
        "output_template": default_output_template(),
    }
