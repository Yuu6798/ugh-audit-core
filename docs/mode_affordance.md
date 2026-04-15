# mode_affordance v1

## Overview

`mode_affordance` is a question-level annotation that describes the expected response form.
While `trap_type` identifies risks in a question (what can go wrong), `mode_affordance`
specifies what a correct response looks like (what should be present).

The two axes are orthogonal: a question can have both a trap and a mode expectation.

## 6 Modes

| Mode | Description | Required Moves |
|------|-------------|----------------|
| `definitional` | Definition/explanation | `define_target`, `set_boundary` |
| `analytical` | Causal/structural analysis | `show_structure_or_causality`, `identify_mechanism_or_condition` |
| `evaluative` | Criteria-based judgment | `state_criteria`, `give_judgment` |
| `comparative` | Comparison/contrast | `name_both_targets`, `compare_on_shared_axis` |
| `critical` | Premise inspection/reframing | `inspect_premise`, `reframe_if_needed` |
| `exploratory` | Possibility mapping | `map_options`, `keep_open_if_needed` |

## Annotation Schema

```json
{
  "mode_affordance": {
    "primary": "critical",
    "secondary": ["analytical"],
    "closure": "qualified",
    "action_required": false
  }
}
```

### Fields

- **primary** (required): One of the 6 modes
- **secondary** (required): List of 0-2 modes. Must not duplicate primary or each other.
  Only include modes whose absence would make a good answer look incomplete.
- **closure**: `closed` | `qualified` | `open`
  - `closed`: Clear conclusion required
  - `qualified`: Conclusion with caveats/conditions
  - `open`: Exploration without forced conclusion
- **action_required**: `true` if the response needs actionable steps/recommendations

## response_mode_signal

The `response_mode_signal` is a non-binding signal computed at runtime that measures how well
a response matches its question's mode_affordance. It follows the `grv` pattern:

- Computed after verdict determination
- Never affects S, C, delta_e, quality_score, or verdict
- Fails silently to `null` if computation errors occur
- Deterministic: same inputs produce same outputs

### Scoring

| Component | Weight | Calculation |
|-----------|--------|-------------|
| primary_score | 0.60 | matched_moves / required_moves for primary mode |
| secondary_scores | 0.20 | average of per-secondary-mode scores |
| closure_score | 0.10 | cue detection for expected closure type |
| action_score | 0.10 | cue detection for actionable content |

When components are absent (no secondary, action_required=false), weights are
redistributed among present components.

### Detection

All detection is regex/cue-list based on Japanese text patterns. No LLM or embedding calls.

- **Move detection**: Each required move has a compiled regex pattern matching Japanese cues
- **Closure detection**: Patterns for conclusion markers, qualification markers, open-ended markers
- **Action detection**: Strong (1.0) and weak (0.5) action cue patterns

### Output

```json
{
  "response_mode_signal": {
    "status": "available",
    "primary_mode": "critical",
    "primary_score": 1.0,
    "secondary_scores": {"analytical": 0.5},
    "closure_expected": "qualified",
    "closure_score": 1.0,
    "action_required": false,
    "action_score": null,
    "overall_score": 0.875,
    "matched_moves": ["inspect_premise", "reframe_if_needed"],
    "missing_moves": ["identify_mechanism_or_condition"],
    "evidence": ["primary(critical): matched inspect_premise, reframe_if_needed"],
    "signal_version": "v1.0"
  }
}
```

When mode_affordance is not available: `{"status": "not_available", ...all null...}`

## Schema

JSON Schema: `schema/mode_affordance_schema_v1.json`

## Implementation

- Annotation data: `data/question_sets/q_metadata_structural_reviewed_102q.jsonl`
- Scorer: `mode_signal.py`
- Integration: `ugh_audit/server.py`, `ugh_audit/mcp_server.py`
- Labeling script: `scripts/label_mode_affordance.py`
