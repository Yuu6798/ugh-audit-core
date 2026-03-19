"""
ugh_audit/scorer/models.py
AuditResult: スコアリング結果のデータモデル
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass(frozen=True)
class AuditResult:
    """UGH指標によるAI回答監査結果"""

    # 入力
    question: str
    response: str
    reference: Optional[str] = None

    # UGH指標
    por: float = 0.0          # Point of Resonance（共鳴度）0-1
    por_fired: bool = False    # PoR発火フラグ（por >= POR_FIRE_THRESHOLD）
    delta_e: float = 0.0      # ΔE 意味ズレ量（0: 完全一致, 1: 完全乖離）
    grv: Dict[str, float] = field(default_factory=dict)  # 語彙重力分布

    # メタデータ
    model_id: str = "unknown"
    session_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # 評価サマリー
    @property
    def meaning_drift(self) -> str:
        """ΔEによる意味ズレ評価"""
        if self.delta_e <= 0.04:
            return "同一意味圏"      # A群基準: ΔE ≤ 0.04
        elif self.delta_e <= 0.10:
            return "軽微なズレ"
        else:
            return "意味乖離"        # 仕様書基準: ΔE > 0.10 = 別コンセプト

    @property
    def dominant_gravity(self) -> Optional[str]:
        """最も強い語彙重力を持つ概念"""
        if not self.grv:
            return None
        return max(self.grv, key=self.grv.get)

    def __repr__(self) -> str:
        return (
            f"AuditResult("
            f"PoR={self.por:.3f}({'🔥' if self.por_fired else '○'}), "
            f"ΔE={self.delta_e:.3f}({self.meaning_drift}), "
            f"grv_top={self.dominant_gravity})"
        )
