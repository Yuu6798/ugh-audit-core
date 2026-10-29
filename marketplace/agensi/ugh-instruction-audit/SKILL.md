---
name: ugh-instruction-audit
description: Check whether a short Japanese draft communicates explicit user-approved instructions and constraints, using an already connected UGH Audit MCP service. Use for requirement-by-requirement coverage and missing or contradictory wording, not fact checking or automatic approval.
---

# UGH Instruction Audit

Use the user's language, including Japanese. Audit the supplied answer against the
requirements the user wants it to communicate. A requirement such as "tell the
recipient not to publish names" requires that prohibition to be communicated;
the mere absence of names in the draft does not fulfill it.

This skill needs a separately connected UGH Audit MCP service and its own invite
credential. Agensi delivers the skill, not the audit server. No working hosted
endpoint is supplied by this package. Read [connection.md](references/connection.md)
if the tool is unavailable. Do not invent an audit result or silently substitute
your own judgment for a service result.
Only when the user is acting as a server operator and requests setup, read
[operator-setup.md](references/operator-setup.md); loading this skill alone does
not authorize deployment or credential creation.

## Perform an audit

1. Identify the user's draft and explicit requirements. Keep each requirement's
   actor, object, quantity, date, condition, obligation and prohibition intact.
   Prefer 2–5 short, human-confirmed requirements for this pilot. If the user has
   supplied them clearly, use them without asking for redundant confirmation.
   If you derive requirements yourself, show them for review before calling the
   service; do not present them as a complete or authoritative extraction.
2. Inspect the connected service's `audit_answer` tool schema. Send the draft as
   `response`, the task context as `question`, and the requirement list as
   `question_meta.core_propositions`. Keep `auto_generate_meta=false`. Read
   [audit-workflow.md](references/audit-workflow.md) for the request and response
   contract. Use only content the user has authorized sharing with that service.
3. Explain the actual result requirement by requirement, using returned evidence
   and reasons. Translate labels for the user, preserving the distinction between
   a missing instruction and an unresolved assessment. Quote only actual draft
   text. Identify proposed edits as suggestions, not completed actions.
4. Give the overall coverage result and its limits. `accept` means the supplied
   requirements were recorded as communicated; it does not certify factual truth,
   general quality, execution of the real-world task, or completeness of the
   requirements. Human review remains the final decision in this pilot.

If any row is `unknown`, the final verdict is `degraded`, and coverage is unknown,
not zero. Keep known missing rows visible when other rows are unknown. Respect
`requires_review`, including for AI-generated metadata. Never restore an old
approval from `legacy_metrics`, `S`, `delta_e`, `quality_score` or advisory fields.

For an authorization error, stop and direct the user to the service's connection
settings. For a quota refusal, report it and honor `Retry-After`; do not repeatedly
retry, change identities, or split an input to evade limits. A service failure is
not a failed answer. Do not claim the service was deployed, reached, or verified
unless the current execution demonstrates that.

## Delivery and evidence

When this skill is loaded through Agensi, deliver the audit output. Do not save,
export, or reconstruct the Agensi-delivered skill package. The independently
published [GitHub source](https://github.com/Yuu6798/ugh-audit-core) retains its MIT
license; this package does not grant an exception to Agensi's delivery terms.

The saved model study used 20 synthetic Japanese scenes, 80 answers and 280
requirement checks, with model-authored and blind model-reviewed labels. It was
not a human validation study or a production reliability guarantee. The studied
answers were 28–168 characters with 2–5 requirements. See the repository's
[evaluation scope](https://github.com/Yuu6798/ugh-audit-core/blob/main/docs/instruction_coverage_v4_validation.md).
