from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from .models import Budget


@dataclass(frozen=True)
class MetaPatchPlan:
    id: str
    primary_fail: str
    normalized_actions: List[str] = field(default_factory=list)
    opcodes: List[str] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    note: str = ""
    source_row: Dict[str, Any] = field(default_factory=dict)


class MetaPatchCompiler:
    def __init__(
        self,
        operator_catalog_path: Optional[Path] = None,
        opcode_catalog_path: Optional[Path] = None,
    ) -> None:
        root = Path(__file__).resolve().parents[2]
        self.operator_catalog_path = operator_catalog_path or root / "registry" / "operator_catalog.yaml"
        self.opcode_catalog_path = opcode_catalog_path or root / "opcodes" / "metapatch_opcodes.yaml"
        self.operator_catalog = yaml.safe_load(self.operator_catalog_path.read_text(encoding="utf-8"))
        self.opcode_catalog = yaml.safe_load(self.opcode_catalog_path.read_text(encoding="utf-8"))

    def normalize_legacy_actions(self, actions: Iterable[str]) -> List[str]:
        mapping = self.operator_catalog.get("legacy_action_map", {})
        normalized: List[str] = []
        for action in actions:
            normalized.extend(mapping.get(action, [action]))
        return normalized

    def actions_from_primary_fail(self, primary_fail: str) -> List[str]:
        mapping = self.operator_catalog.get("primary_fail_map", {})
        return list(mapping.get(primary_fail, []))

    def compile_row(self, row: Dict[str, Any], extra_legacy_actions: Optional[Iterable[str]] = None) -> MetaPatchPlan:
        primary_fail = row.get("primary_fail", "none")
        opcodes = self.actions_from_primary_fail(primary_fail)
        normalized = self.normalize_legacy_actions(extra_legacy_actions or [])
        merged = []
        for opcode in [*opcodes, *normalized]:
            if opcode not in merged:
                merged.append(opcode)
        budget = self._build_budget(merged)
        return MetaPatchPlan(
            id=row.get("id", "unknown"),
            primary_fail=primary_fail,
            normalized_actions=normalized,
            opcodes=merged,
            budget=budget,
            note=row.get("note", ""),
            source_row=row,
        )

    def compile_csv(self, csv_path: Path, extra_actions_by_id: Optional[Dict[str, List[str]]] = None) -> List[MetaPatchPlan]:
        plans: List[MetaPatchPlan] = []
        with csv_path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                extras = (extra_actions_by_id or {}).get(row.get("id", ""), [])
                plans.append(self.compile_row(row, extras))
        return plans

    def _build_budget(self, opcodes: List[str]) -> Budget:
        catalog = self.opcode_catalog.get("opcodes", {})
        cost = sum(int(catalog.get(op, {}).get("cost", 1)) for op in opcodes)
        return Budget(cost=cost, opcodes=opcodes, extra={"source": "metapatch"})
