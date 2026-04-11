"""
ugh_audit/metadata_policy.py
メタデータ昇格ポリシーと表示用ヘルパー。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class PromotionPolicy:
    min_usage_count: int = 3
    min_accepted_count: int = 2
    min_confidence: float = 0.7
    max_rejected_count_for_promotion: int = 0
    rejected_count_threshold: int = 3


DEFAULT_PROMOTION_POLICY = PromotionPolicy()


def load_promotion_policy(policy_path: Path | None = None) -> PromotionPolicy:
    path = policy_path or (Path(__file__).resolve().parent.parent / "config" / "metadata_promotion_policy.json")
    if not path.exists():
        return DEFAULT_PROMOTION_POLICY
    data = json.loads(path.read_text(encoding="utf-8"))
    valid_keys = {f.name for f in PromotionPolicy.__dataclass_fields__.values()}
    merged = {**asdict(DEFAULT_PROMOTION_POLICY), **{k: v for k, v in data.items() if k in valid_keys}}
    return PromotionPolicy(**merged)


def format_recommendation_reasons(reasons: list[str]) -> str:
    labels = {
        "gold_metadata": "gold metadata",
        "usage_count_threshold": "usage threshold met",
        "accepted_count_threshold": "accepted threshold met",
        "confidence_threshold": "confidence threshold met",
        "no_rejections": "no rejections observed",
        "rejected_count_threshold": "rejection threshold met",
    }
    if not reasons:
        return "No promotion signals yet."
    return ", ".join(labels.get(reason, reason) for reason in reasons)
