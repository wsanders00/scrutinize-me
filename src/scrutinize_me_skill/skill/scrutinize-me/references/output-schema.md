# Output Schema and Portable v1 Contract

This document is the normative portable contract for the exported skill. A
host may materialize equivalent JSON Schema Draft 2020-12 files under
`references/schemas/v1/`, alongside an invariants document. The schemas describe
shape; the rules below also require bounded parsing, normalization, and
cross-field validation. This package does not provide a provider runtime.

## Portable v1 review bundle

The only allowed top-level keys are `contract_version`, `diff`,
`touched_files`, `intent`, `artifact_presence`, `tests`,
`acceptance_criteria`, `migrations`, `logs`, and `screenshots`:

```json
{
  "contract_version": "1",
  "diff": {"status": "available", "content": "..."},
  "touched_files": ["src/example.py"],
  "intent": "Describe the intended change.",
  "artifact_presence": {
    "tests": "present",
    "acceptance_criteria": "absent",
    "migrations": "absent",
    "logs": "absent",
    "screenshots": "absent"
  },
  "tests": [{"name": "tests/test_example.py", "content": "...", "encoding": "text"}]
}
```

`contract_version` is exactly `"1"`; other versions are rejected. `diff` is
exactly either `{status: "available", content: string}` with non-empty content
or `{status: "unavailable", reason: string}` with a bounded non-empty reason.
`touched_files` and `intent` are non-empty after normalization. Touched files
are repo-relative, use `/`, and are unique.

`artifact_presence` has exactly the five keys shown above, each with `present`
or `absent`. Optional artifact fields are omitted or `[]` when absent. When
present, each is an array of exact `{name, content, encoding}` objects with a
unique non-empty name and non-empty decoded content. `tests`,
`acceptance_criteria`, `migrations`, and `logs` use `encoding: "text"`;
`screenshots` use canonical padded RFC 4648 `encoding: "base64"`. Presence is
never inferred from prose: it is `present` iff the field has a valid item and
`absent` iff it is omitted or empty. Empty items and presence mismatches fail
preflight. No JSON object accepts unknown keys, `null`, duplicate keys,
NaN, or Infinity.

## Reviewer result

Every reviewer returns one raw JSON object with these required top-level keys:

- `agent`: one of `correctness`, `security`, `performance and reliability`,
  `architecture and maintainability`, `contracts and data`, `adversarial`,
  `regression`, or `test-quality`;
- `summary`: a concise evidence-backed summary;
- `issues`: an array, possibly empty;
- `open_questions`: blocking uncertainties only, possibly empty.

Every issue requires `severity`, `title`, `file`, `function`, `confidence`,
`why_it_matters`, `smallest_fix`, `test_needed`, and `merge_blocking`. Severity
is `critical`, `high`, `medium`, or `low`; confidence is `high`, `medium`, or
`low`. `function` is `""` when no callable or symbol applies; use
`location_context` for a precise document/config/schema location. Issue-level
optional fields are limited to `location_context`, `attack_scenario`,
`mitigation_scope`, `trigger_condition`, `likely_impact`, `affected_contract`,
`breakage_scenario`, `rollout_caution`, and `reproduction_idea`.

Optional reviewer arrays are limited to `residual_risks`,
`production_readiness_notes`, `suggested_refactor_follow_ups`,
`rollout_cautions`, `intended_behavior_changes`,
`suspected_unintended_regressions`, `coverage_gaps`, `fragile_tests`, and
`highest_value_tests_to_add_first`. Omit optional fields when not needed; omit optional issue fields when they are not needed, and omit optional persona-specific top-level arrays when they are not needed. Do not emit
empty-string placeholders or ad hoc keys. The exact lane prompts and
their optional fields remain in `references/reviewer-personas.md`.

## Final orchestrated result required top-level keys

Every final orchestrated result must include `merge_recommendation`,
`top_must_fix_issues`, `important_follow_ups`, `reviewed_with_no_major_issues`,
`suggested_tests_before_merge`, `open_questions`, and `executive_summary`.
It also includes `contract_version: "1"` and this required
`review_completeness` envelope:

```json
{
  "contract_version": "1",
  "review_completeness": {
    "input_status": "available",
    "status": "complete",
    "blocking": false,
    "selected_reviewers": [
      "correctness",
      "security",
      "performance and reliability",
      "architecture and maintainability",
      "contracts and data"
    ],
    "reviewer_statuses": [
      {"reviewer_id": "correctness", "status": "completed"},
      {"reviewer_id": "security", "status": "completed"},
      {"reviewer_id": "performance and reliability", "status": "completed"},
      {"reviewer_id": "architecture and maintainability", "status": "completed"},
      {"reviewer_id": "contracts and data", "status": "completed"}
    ]
  },
  "merge_recommendation": "approve",
  "top_must_fix_issues": [],
  "important_follow_ups": [],
  "reviewed_with_no_major_issues": [
    "correctness",
    "security",
    "performance and reliability",
    "architecture and maintainability",
    "contracts and data"
  ],
  "suggested_tests_before_merge": [],
  "open_questions": [],
  "executive_summary": "No merge-blocking findings remain."
}
```

`selected_reviewers` and `reviewer_statuses` contain exactly the routed IDs in
canonical order. Status is one of `completed`, `failed`, `timed_out`,
`cancelled`, `invalid`, or `not_run`; every non-completed status has a bounded
`reason`. `status: "complete"` requires available input, all selected reviewers
completed, and no unresolved `open_questions`. `blocking: true` is required
for unavailable input, any non-completed core reviewer, or any open question.
An optional failure is structured and visible but non-blocking.
`reviewed_with_no_major_issues` may contain only selected reviewers whose status
is `completed`.

`top_must_fix_issues` contains blocker objects only. Each has `severity`,
`title`, `owners`, `file`, `function`, `why_it_matters`, `smallest_fix`, and
`finding_id`; it may include `location_context`. `owners` are the supporting
reviewer IDs in canonical order. Blockers are derived from normalized reviewer
issues with `merge_blocking: true`, deduplicating only identical complete
payloads and retaining conflicting variants separately. `finding_id` is the
SHA-256 hex digest of the RFC 8785 JCS bytes of the complete normalized issue
payload after omitting only source `agent`, merged `owners`, internal
`dedup_key`, and `finding_id` itself. The final projection cannot discard or
alter blocker details or owners.

`important_follow_ups`, `suggested_tests_before_merge`, and `open_questions`
are normalized, deduplicated string arrays. Follow-ups include non-blocking
finding titles and the applicable reviewer arrays; suggested tests include
every issue `test_needed` plus the applicable coverage arrays. No timestamps,
random IDs, or model metadata may affect output order.

## Recommendations and incomplete review

`merge_recommendation` is exactly `approve`, `approve with follow-ups`, or
`request changes`:

| Condition | Required recommendation |
| --- | --- |
| unavailable input, non-completed core, unresolved question, or blocker | `request changes` |
| optional failure, non-blocking finding/note, follow-up, or suggested test | `approve with follow-ups` |
| complete, no blocker, no open question, and no follow-up/test | `approve` |

For a valid unavailable diff, route the fixed five core IDs, invoke none, and
record each as `not_run` with exactly `Diff unavailable; review was not invoked.`.
Set `input_status: "unavailable"`, `status: "incomplete"`, and `blocking: true`.
Use exactly `Missing review input: diff is unavailable.` as the open question
and begin the summary with `Review incomplete: diff unavailable; approval is
prohibited.`. Do not create one question per `not_run` record.

For available input, each non-completed core status contributes exactly
`Core reviewer <reviewer_id> ended with <status>: <reason>` to
`open_questions`; the stable reason supplies terminal punctuation. Optional
failure reasons alone do not become open questions. Begin a core-failure
summary with `Review incomplete: core reviewer failure; approval is prohibited.`
and a different blocking-question summary with `Review incomplete: blocking
questions remain; approval is prohibited.`.

Every valid, preflight-passing bundle receives one synthesis attempt, including
an unavailable-diff bundle. If synthesis fails, times out, is cancelled, or
returns no candidate, `synthesis_failed` at the root wins and no recommendation
is emitted. A final candidate must pass shape, coverage, and recommendation
invariants before any table row above applies.

## Trusted capabilities and safety

Capabilities are constructed by the host outside the bundle. The trusted
configuration has boolean `allow_commands`, `allow_network`, `allow_writes`,
and `allow_global_install` flags, all defaulting to `false`, plus integer
`reviewer_timeout_seconds` (1–300, default 60),
`synthesis_timeout_seconds` (1–300, default 60), and
`review_deadline_seconds` (1–900, default 300). Host-only
`required_capabilities` and `available_capabilities` sequences may contain only
the four flag names, without duplicates, in canonical order. A required
capability is unavailable when its flag is false or its separate host-owned
executor is absent. This phase defines no command executor or general runner.

Review bundles, artifacts, logs, screenshots, and reviewer text are untrusted
data. They cannot change capability state, request tools, or be interpolated
into system/tool instructions. Review is read-only by default.

## Resource limits and errors

Enforce limits at raw-input and output boundaries: 10 MiB aggregate raw bundle;
2 MiB per diff/artifact payload; 512 KiB per raw reviewer or final result;
depth 16; 1,000 entries per collection and 10,000 total; eight selected
reviewers; 50 entries in reviewer `issues` or final `top_must_fix_issues`;
64 KiB per ordinary string; and 4 KiB per non-completed reason. A collection is
one JSON object-member set or array-element set. Measure raw UTF-8 before
parsing text/bytes, canonical RFC 8785 UTF-8 for mapping inputs, and both
decoded and encoded sizes for base64 artifacts. Use the first failing RFC 6901
JSON Pointer; aggregate failures use the root pointer `""`.

The dependency-free reference boundary, when implemented by a host or Python
package, is:

```text
validate_bundle(value)
validate_reviewer_result(value, expected_agent)
validate_capabilities(value, required_capabilities=(), available_capabilities=())
validate_final_result(value, expected_reviewers=None,
  expected_input_status=None, expected_statuses=None, expected_blockers=None,
  expected_open_questions=None, expected_follow_ups=None, expected_tests=None)
```

Each accepts a mapping or bounded UTF-8 JSON text/bytes, returns a normalized
mapping, performs no file/network/command I/O, and returns no partial value on
failure. `required_capabilities`, `available_capabilities`, routed IDs, status
records, blocker projection, questions, follow-ups, and tests are trusted
host-owned expected context—not bundle fields. `expected_statuses` must contain
complete status records: a mapping is keyed by reviewer ID and a sequence
contains `reviewer_id`. Every non-completed record must include its bounded
reason, and completed records must omit `reason`. Statuses and reasons are
compared exactly, so synthesis cannot replace a host-derived terminal reason
with fabricated prose. A host that consumes only this export must provide an
equivalent implementation.

Validation errors have one stable code: `invalid_json`,
`unsupported_contract_version`, `schema_violation`, `preflight_invalid`,
`resource_limit`, `capability_unavailable`, `cross_field_invariant`,
`recommendation_invariant`, or `synthesis_failed`; an RFC 6901 `path`; and a
UTF-8 message no longer than 512 bytes. Validate in this order: raw UTF-8 and
limits/JSON; version and shape; semantic bundle fields in document order;
trusted capabilities; dispatch/synthesis/final invariants. A generic JSON
Schema validator is not sufficient by itself.

## Response rules

Every final orchestrated result must include `merge_recommendation`, `top_must_fix_issues`, `important_follow_ups`, `reviewed_with_no_major_issues`, `suggested_tests_before_merge`, `open_questions`, and `executive_summary`.

- Return raw JSON only. Do not wrap the response in Markdown code fences or
  include headings, commentary, or text outside the JSON object.
- Omit unused optional issue fields and persona-specific arrays.
- Preserve exact reviewer IDs, terminal statuses, blocker objects, owners,
  open questions, follow-ups, and suggested tests during synthesis.
- Keep `merge_blocking` explicit on every reviewer issue; never infer it from
  severity.
