from __future__ import annotations

import json
from itertools import product
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUESTION_META_PATH = ROOT / "data" / "question_sets" / "ugh-audit-100q-v3-1.json.txtl.txt"
OUTPUT_PATH = ROOT / "analysis" / "metadata_policy" / "tuned_promotion_policy.json"


def load_questions() -> list[dict]:
    items = []
    with open(QUESTION_META_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def score_policy(questions: list[dict], usage: int, accepted: int, confidence: float) -> dict:
    total = len(questions)
    promotable = 0
    avg_props = 0.0
    for item in questions:
        proposition_count = len(item.get("core_propositions") or [])
        avg_props += proposition_count
        simulated_confidence = min(1.0, 0.45 + 0.1 * min(proposition_count, 4))
        simulated_usage = max(1, proposition_count)  # 命題数を usage proxy として使用
        if simulated_usage >= usage and proposition_count >= accepted and simulated_confidence >= confidence:
            promotable += 1
    avg_props = avg_props / total if total else 0.0
    coverage = promotable / total if total else 0.0
    conservatism_penalty = abs(usage - 3) * 0.03 + abs(accepted - 2) * 0.05
    score = coverage - conservatism_penalty + min(avg_props, 4) * 0.01
    return {
        "min_usage_count": usage,
        "min_accepted_count": accepted,
        "min_confidence": confidence,
        "max_rejected_count_for_promotion": 0,
        "rejected_count_threshold": 3,
        "coverage": round(coverage, 4),
        "avg_propositions": round(avg_props, 4),
        "score": round(score, 4),
    }


def main() -> None:
    questions = load_questions()
    candidates = [
        score_policy(questions, usage, accepted, confidence)
        for usage, accepted, confidence in product((2, 3, 4), (1, 2, 3), (0.6, 0.7, 0.8))
    ]
    best = max(candidates, key=lambda item: item["score"])
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(best, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
