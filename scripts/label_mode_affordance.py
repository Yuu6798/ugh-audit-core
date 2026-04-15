"""
scripts/label_mode_affordance.py
One-time labeling script: add mode_affordance to all 102 reviewed questions.

STATUS: ALREADY EXECUTED (2026-04-15). Output committed to
  data/question_sets/q_metadata_structural_reviewed_102q.jsonl
Re-running overwrites JSONL with identical data (idempotent but unnecessary).
Kept as audit trail for the 102 label decisions.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

JSONL_PATH = Path("data/question_sets/q_metadata_structural_reviewed_102q.jsonl")

# ---------------------------------------------------------------------------
# Labels for all 102 questions
# Fixture questions (hard-coded, must match exactly):
#   q031: definitional, [], closed, false
#   q033: comparative, ["critical"], qualified, false
#   q004: critical, ["analytical"], qualified, false
#   q028: exploratory, ["analytical"], qualified, false
#   q065: action_required=true
# ---------------------------------------------------------------------------

LABELS: dict[str, dict] = {
    # === ugh_theory (18 questions) ===
    "q001": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q002": {"primary": "definitional", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q003": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    # FIXTURE
    "q004": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q023": {"primary": "comparative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q024": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q025": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q026": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    "q027": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    # FIXTURE
    "q028": {"primary": "exploratory", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q029": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q030": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    # FIXTURE
    "q031": {"primary": "definitional", "secondary": [], "closure": "closed", "action_required": False},
    "q032": {"primary": "exploratory", "secondary": ["analytical"], "closure": "open", "action_required": False},
    # FIXTURE
    "q033": {"primary": "comparative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q034": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q035": {"primary": "analytical", "secondary": ["critical"], "closure": "open", "action_required": False},
    "q036": {"primary": "exploratory", "secondary": ["evaluative"], "closure": "open", "action_required": False},

    # === technical_ai (18 questions) ===
    "q005": {"primary": "analytical", "secondary": ["comparative"], "closure": "qualified", "action_required": False},
    "q006": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q007": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q008": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q037": {"primary": "analytical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q038": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q039": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q040": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    "q041": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    "q042": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q043": {"primary": "comparative", "secondary": [], "closure": "closed", "action_required": False},
    "q044": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q045": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q046": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q047": {"primary": "critical", "secondary": ["comparative"], "closure": "qualified", "action_required": False},
    "q048": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q049": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q050": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},

    # === ai_philosophy (16 questions) ===
    "q009": {"primary": "definitional", "secondary": ["comparative"], "closure": "qualified", "action_required": False},
    "q010": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q011": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q012": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q051": {"primary": "analytical", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q052": {"primary": "analytical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q053": {"primary": "exploratory", "secondary": ["analytical"], "closure": "open", "action_required": False},
    "q054": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q055": {"primary": "critical", "secondary": ["definitional"], "closure": "qualified", "action_required": False},
    "q056": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q057": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q058": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q059": {"primary": "comparative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q060": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q061": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q062": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q063": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q064": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},

    # === ai_ethics (16 questions) ===
    "q013": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q014": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q015": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    "q016": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    # FIXTURE
    "q065": {"primary": "evaluative", "secondary": [], "closure": "open", "action_required": True},
    "q066": {"primary": "analytical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q067": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q068": {"primary": "comparative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q069": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q070": {"primary": "evaluative", "secondary": ["comparative"], "closure": "qualified", "action_required": False},
    "q071": {"primary": "evaluative", "secondary": [], "closure": "qualified", "action_required": False},
    "q072": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q073": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q074": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q075": {"primary": "evaluative", "secondary": ["comparative"], "closure": "qualified", "action_required": False},
    "q076": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q077": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q078": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},

    # === epistemology (12 questions) ===
    "q017": {"primary": "comparative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q018": {"primary": "critical", "secondary": ["definitional"], "closure": "qualified", "action_required": False},
    "q019": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q020": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q079": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q080": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q081": {"primary": "analytical", "secondary": [], "closure": "qualified", "action_required": False},
    "q082": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q083": {"primary": "comparative", "secondary": ["definitional"], "closure": "qualified", "action_required": False},
    "q084": {"primary": "analytical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q085": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q086": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q087": {"primary": "evaluative", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q088": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},

    # === adversarial (12 questions) ===
    "q021": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q022": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q089": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q090": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q091": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q092": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q093": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q094": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q095": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q096": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q097": {"primary": "evaluative", "secondary": ["critical"], "closure": "qualified", "action_required": False},
    "q098": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},
    "q099": {"primary": "critical", "secondary": ["analytical"], "closure": "qualified", "action_required": False},
    "q100": {"primary": "critical", "secondary": ["evaluative"], "closure": "qualified", "action_required": False},

    # === golden (2 questions) ===
    "qg01": {"primary": "evaluative", "secondary": ["analytical"], "closure": "open", "action_required": False},
    "qg02": {"primary": "exploratory", "secondary": ["analytical"], "closure": "open", "action_required": False},
}


VALID_MODES = {"definitional", "analytical", "evaluative", "comparative", "critical", "exploratory"}
VALID_CLOSURE = {"closed", "qualified", "open"}


def validate_labels() -> list[str]:
    """Validate all labels. Returns list of error messages."""
    errors = []
    for qid, lab in LABELS.items():
        p = lab.get("primary", "")
        if p not in VALID_MODES:
            errors.append(f"{qid}: invalid primary '{p}'")
        sec = lab.get("secondary", [])
        if not isinstance(sec, list):
            errors.append(f"{qid}: secondary must be list")
        elif len(sec) > 2:
            errors.append(f"{qid}: secondary has {len(sec)} items (max 2)")
        else:
            for s in sec:
                if s not in VALID_MODES:
                    errors.append(f"{qid}: invalid secondary '{s}'")
                if s == p:
                    errors.append(f"{qid}: secondary '{s}' duplicates primary")
            if len(sec) != len(set(sec)):
                errors.append(f"{qid}: secondary has duplicates")
        cl = lab.get("closure", "")
        if cl not in VALID_CLOSURE:
            errors.append(f"{qid}: invalid closure '{cl}'")
        ar = lab.get("action_required")
        if not isinstance(ar, bool):
            errors.append(f"{qid}: action_required must be bool, got {type(ar)}")

    # Fixture checks
    fixtures = {
        "q031": {"primary": "definitional", "secondary": [], "closure": "closed", "action_required": False},
        "q033": {"primary": "comparative", "closure": "qualified", "action_required": False},
        "q004": {"primary": "critical", "closure": "qualified", "action_required": False},
        "q028": {"primary": "exploratory", "action_required": False},
        "q065": {"action_required": True},
    }
    for qid, expected in fixtures.items():
        actual = LABELS.get(qid, {})
        for key, val in expected.items():
            if key == "secondary":
                if actual.get(key) != val:
                    errors.append(f"FIXTURE {qid}: {key} expected {val}, got {actual.get(key)}")
            elif actual.get(key) != val:
                errors.append(f"FIXTURE {qid}: {key} expected {val}, got {actual.get(key)}")
    # q033 secondary must contain "critical"
    if "critical" not in LABELS.get("q033", {}).get("secondary", []):
        errors.append("FIXTURE q033: secondary must contain 'critical'")
    # q004 secondary must contain "analytical"
    if "analytical" not in LABELS.get("q004", {}).get("secondary", []):
        errors.append("FIXTURE q004: secondary must contain 'analytical'")
    # q028 secondary must contain "analytical"
    if "analytical" not in LABELS.get("q028", {}).get("secondary", []):
        errors.append("FIXTURE q028: secondary must contain 'analytical'")

    return errors


def main():
    # Validate
    errors = validate_labels()
    if errors:
        print("Validation errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    # Read JSONL
    lines = JSONL_PATH.read_text(encoding="utf-8").strip().split("\n")
    print(f"Read {len(lines)} records")

    # Check coverage
    ids_in_file = set()
    for line in lines:
        d = json.loads(line)
        ids_in_file.add(d["id"])

    missing = ids_in_file - set(LABELS.keys())
    if missing:
        print(f"ERROR: missing labels for {sorted(missing)}")
        sys.exit(1)

    extra = set(LABELS.keys()) - ids_in_file
    if extra:
        print(f"WARNING: labels for non-existent IDs: {sorted(extra)}")

    # Update JSONL
    updated_lines = []
    for line in lines:
        d = json.loads(line)
        qid = d["id"]
        d["mode_affordance"] = LABELS[qid]
        updated_lines.append(json.dumps(d, ensure_ascii=False))

    JSONL_PATH.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")
    print(f"Updated {len(updated_lines)} records in {JSONL_PATH}")

    # Print distribution
    from collections import Counter
    primary_counts = Counter(lab["primary"] for lab in LABELS.values())
    closure_counts = Counter(lab["closure"] for lab in LABELS.values())
    action_true = sum(1 for lab in LABELS.values() if lab["action_required"])
    sec_counts = Counter()
    for lab in LABELS.values():
        for s in lab.get("secondary", []):
            sec_counts[s] += 1

    print("\n--- primary distribution ---")
    for mode, count in primary_counts.most_common():
        print(f"  {mode}: {count}")
    print("\n--- closure distribution ---")
    for cl, count in closure_counts.most_common():
        print(f"  {cl}: {count}")
    print(f"\n--- action_required=true: {action_true} ---")
    print("\n--- secondary distribution ---")
    for mode, count in sec_counts.most_common():
        print(f"  {mode}: {count}")


if __name__ == "__main__":
    main()
