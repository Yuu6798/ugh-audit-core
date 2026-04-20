from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from audit import audit  # noqa: E402


QUESTION_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.jsonl"
RESPONSES_PATH = ROOT / "data" / "phase_c_v0" / "phase_c_raw.jsonl"


def _load_question_meta(question_id: str) -> dict:
    with QUESTION_META_PATH.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"] == question_id:
                return row
    raise KeyError(question_id)


def _load_response(question_id: str, temperature: float = 0.0) -> str:
    with RESPONSES_PATH.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["id"] == question_id and row["temperature"] == temperature:
                return row["response"]
    raise KeyError((question_id, temperature))


def _run(question_id: str) -> dict:
    meta = _load_question_meta(question_id)
    response = _load_response(question_id, 0.0)
    return audit(question_id, response, meta)


def test_q016_p2_blocked_by_required_chunks() -> None:
    """q016[2] は fr=0.30 で閾値を通過するが required_chunks ガードで miss"""
    result = _run("q016")
    assert 2 in result["evidence"]["miss_ids"]


def test_relaxed_tier1_keeps_q042_p1_blocked() -> None:
    result = _run("q042")
    assert 1 in result["evidence"]["miss_ids"]


def test_relaxed_tier1_keeps_q061_p2_blocked() -> None:
    result = _run("q061")
    assert 2 in result["evidence"]["miss_ids"]
