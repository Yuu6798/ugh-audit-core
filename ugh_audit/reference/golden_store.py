"""
ugh_audit/reference/golden_store.py
Reference セット管理 — 暫定基準（研究段階）

暫定採用基準（Clawによる判断）:
    - PoR threshold: 0.82（ugh3-metrics-libデフォルト）
    - ΔE同一意味圏: <= 0.04（イラスト実験A群平均から流用）
    - ΔE意味乖離:   > 0.10（SVP仕様書定義）
    - grv reference: Phase 3対話ログのモデル別語彙重力分布から初期golden設定

検証・修正方針:
    ログ蓄積後にパターンが見えたら随時proposalを提出し承認を得て更新する。
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

DEFAULT_GOLDEN_PATH = Path.home() / ".ugh_audit" / "golden_store.json"

# 暫定goldenリファレンス（Phase 3対話ログから抽出）
# 各モデルの「意味的誠実さ」を示す回答パターン
_INITIAL_GOLDEN: Dict[str, dict] = {
    "ugh_definition": {
        "question": "AIは意味を持てるか？",
        "reference": (
            "AIは意味を『持つ』のではなく、"
            "意味位相空間で『共振（Co-resonance）』する動的プロセスです。"
            "意識は機能的意味の必要条件ではない。"
        ),
        "source": "IMM v1.0 (Phase 3 AI-to-AI Dialogue)",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.04,
    },
    "por_definition": {
        "question": "PoRとは何か？",
        "reference": (
            "PoR（Point of Resonance）は意味の発火点・共鳴点。"
            "不可分な要素の交点として定義される。"
            "例：逆手納刀における刃背×鞘口×親指の交点。"
        ),
        "source": "RPE SVP仕様解説書",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.05,
    },
    "delta_e_definition": {
        "question": "ΔEとは何か？",
        "reference": (
            "ΔEは目標と生成物の意味ズレ量。"
            "0.04以下はほぼ同一構図（同一意味圏）、"
            "0.10以上は別コンセプトと定義される。"
        ),
        "source": "RPE SVP仕様解説書",
        "por_floor": 0.82,
        "delta_e_ceiling": 0.04,
    },
}


@dataclass
class GoldenEntry:
    question: str
    reference: str
    source: str
    por_floor: float = 0.82
    delta_e_ceiling: float = 0.04
    tags: list = field(default_factory=list)


class GoldenStore:
    """
    Referenceセット管理

    研究段階につき暫定基準を採用。
    ログ蓄積後のパターン分析を経て随時更新する。
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = path or DEFAULT_GOLDEN_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._store: Dict[str, GoldenEntry] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, val in data.items():
                self._store[key] = GoldenEntry(**val)
        else:
            # 初期goldenをロード
            for key, val in _INITIAL_GOLDEN.items():
                self._store[key] = GoldenEntry(**val)
            self._save()

    def _save(self) -> None:
        data = {
            k: {
                "question": v.question,
                "reference": v.reference,
                "source": v.source,
                "por_floor": v.por_floor,
                "delta_e_ceiling": v.delta_e_ceiling,
                "tags": v.tags,
            }
            for k, v in self._store.items()
        }
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def get(self, key: str) -> Optional[GoldenEntry]:
        return self._store.get(key)

    def add(self, key: str, entry: GoldenEntry) -> None:
        self._store[key] = entry
        self._save()

    def find_reference(self, question: str) -> Optional[str]:
        """質問に最も近いreferenceを返す（簡易マッチング）"""
        for entry in self._store.values():
            # 部分一致で近似
            if any(w in question for w in entry.question.split()):
                return entry.reference
        return None

    def list_keys(self):
        return list(self._store.keys())
