# Request and interpretation

Use the tool schema advertised by the connected UGH Audit service. Tool names may
be prefixed by the agent host; the underlying tool is `audit_answer`.

Example request, containing synthetic data only:

```json
{
  "question": "書類送付の案内文を点検してください。",
  "response": "申請書は金曜日までに担当者へ送付してください。",
  "question_meta": {
    "core_propositions": [
      "申請書を金曜日までに送付する",
      "申請書の送付先は担当者である"
    ]
  },
  "auto_generate_meta": false
}
```

No invite token, Anthropic API key, owner identifier, supplied verdict, or supplied
judge belongs in this tool request. The connection sends the invite in
`Authorization` and the user's own provider key in `X-UGH-Anthropic-Key`.
Never move a secret header into tool arguments to work around a host limitation.
The service derives
ownership from the credential. Never ask to inspect another user's history.

The returned contract must be schema `4.0.0`,
`decision_scope=instruction_coverage_v2`, and
`metrics_scope=semantic_coverage_only`. If the connected service advertises a
different contract, report incompatibility instead of interpreting it as this one.

| Requirement results | Final verdict | Coverage C |
|---|---|---|
| At least one requirement; all fulfilled | accept | 1.0 |
| Missing requirements; none unknown | rewrite | fulfilled / all requirements |
| Any unknown, invalid requirements, or failed assessment | degraded | null |

`missing_kind=absence` means a required instruction or part of it is missing.
`missing_kind=contradiction` means the draft explicitly conflicts with it.
`unknown` can reflect ambiguous language or a system failure; use the returned
reason to distinguish them. Do not remove unknown rows from the denominator.

Use `semantic_matching.results` to present requirement text, status, evidence and
reason. A concise output can contain the overall verdict, a table of those rows,
and suggested edits for confirmed omissions or contradictions. State whether
`requires_review` is true. Do not reproduce request IDs, raw provider responses,
or unrelated history unless the user needs them for a specific diagnosis.

Upper `S`, `delta_e` and `quality_score` are null by design. Old numerical
diagnostics are nonbinding and belong to `legacy_metrics`. `is_reliable=true`
means the requirement assessments are resolved; it is not a truth or safety seal.

Server guardrails allow at most 16 requirements, 2,000 characters per requirement
and 32,000 characters in the answer. These limits are not a validated accuracy
range. Do not truncate or drop requirements to manufacture acceptance. Prefer
the short-input pilot scope in SKILL.md, and ask the user to scope a larger task
explicitly when needed.

The semantic check has a 25-second budget after requirement preparation and a
maximum 10-second request timeout per requirement. This is not an end-to-end
response-time guarantee. The service makes external Anthropic calls billed to the
user's own API account, even when the requirements are explicit; no result
should be described as entirely local or offline.

New degraded audits are not saved in ordinary history. Explain `saved_id=null`
without claiming a record was persisted. Optional history tools may be used only
for the user's requested records on the authenticated connection.
