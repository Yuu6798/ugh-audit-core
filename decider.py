"""decider.py — 判定層

State + Evidence → policy + budget を生成する。
decision logic は output_schema.yaml に定義済み。
repair_order は runtime_repair_opcodes.yaml から選択する。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

import yaml

from ugh_calculator import Evidence, State

# --- opcodes のロード ---
_OPCODES_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "opcodes"


def _load_opcodes() -> Dict[str, dict]:
    """runtime_repair_opcodes.yaml をロードする"""
    path = _OPCODES_DIR / "runtime_repair_opcodes.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("opcodes", {})


def _decision(state: State) -> str:
    """decision logic（検証済み: 20/20一致）

    if delta_e_bin == 1                    → accept
    if delta_e_bin == 2 and C_bin >= 2     → accept
    if delta_e_bin == 2 and C_bin == 1     → rewrite
    if delta_e_bin == 3                    → rewrite
    if delta_e_bin == 4                    → regenerate
    """
    if state.delta_e_bin == 1:
        return "accept"
    if state.delta_e_bin == 2:
        if state.C_bin >= 2:
            return "accept"
        return "rewrite"
    if state.delta_e_bin == 3:
        return "rewrite"
    return "regenerate"


def _build_repair_order(
    evidence: Evidence,
    state: State,
    opcodes: Dict[str, dict],
) -> List[str]:
    """repair_order を生成する

    ルール:
    - f2検出 → PRESERVE_TERM + BLOCK_REINTERPRETATION
    - f4検出 → QUESTION_PREMISE
    - f3検出 → 該当演算子族の required_action に対応する opcode
    - C_bin < 3 → miss_ids分の ADD_PROPOSITION
    - 末尾に必ず STOP_REWRITE
    """
    order: List[str] = []

    # f2: 用語捏造
    if evidence.f2_unknown > 0:
        order.append("PRESERVE_TERM")
        order.append("BLOCK_REINTERPRETATION")

    # f4: 前提受容 — trap_typeに応じた修復opcodeを選択
    if evidence.f4_premise > 0:
        if evidence.f4_trap_type == "binary_reduction":
            order.append("CHALLENGE_BINARY")
            order.append("EXPAND_ALTERNATIVES")
        else:
            order.append("QUESTION_PREMISE")

    # f3: 演算子無処理 — 検出された演算子族に応じた修復opcodeを選択
    if evidence.f3_operator > 0:
        _FAMILY_OPCODE_MAP = {
            "universal_positive": "QUALIFY_UNIVERSAL",
            "universal_negative": "QUALIFY_UNIVERSAL",
            "exclusive": "EXAMINE_SCOPE",
            "conditional": "EXAMINE_SCOPE",
            "comparative": "EXAMINE_SCOPE",
            "negative_question": "EXAMINE_SCOPE",
            "causal": "EXAMINE_PREMISE",
            "reason_request_with_premise": "EXAMINE_PREMISE",
        }
        opcode = _FAMILY_OPCODE_MAP.get(evidence.f3_operator_family, "QUALIFY_UNIVERSAL")
        order.append(opcode)

    # f1: 主題逸脱
    if evidence.f1_anchor > 0:
        order.append("RESTORE_ANCHOR")

    # 命題補完
    if state.C_bin < 3:
        for _ in evidence.miss_ids:
            order.append("ADD_PROPOSITION")

    # 末尾に STOP_REWRITE
    order.append("STOP_REWRITE")

    return order


def _compute_budget(repair_order: List[str], opcodes: Dict[str, dict]) -> Dict:
    """budget を計算する"""
    total_cost = 0
    for opcode_name in repair_order:
        opcode_def = opcodes.get(opcode_name, {})
        total_cost += opcode_def.get("cost", 0)
    return {
        "total_cost": total_cost,
        "opcode_count": len(repair_order),
    }


def decide(state: State, evidence: Evidence) -> dict:
    """判定層: State + Evidence → policy + budget

    全計算が決定的。同じ入力なら同じ出力。
    """
    opcodes = _load_opcodes()

    decision = _decision(state)
    repair_order = _build_repair_order(evidence, state, opcodes)
    budget = _compute_budget(repair_order, opcodes)

    return {
        "policy": {
            "decision": decision,
            "repair_order": repair_order,
        },
        "budget": budget,
    }
