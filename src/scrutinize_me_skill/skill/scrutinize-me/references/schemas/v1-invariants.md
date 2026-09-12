# v1 contract invariants

The JSON Schemas in `schemas/v1/` define the portable shape. These rules are
normative and are implemented by `scrutinize_me_skill.contracts`; a host that
uses another validator must enforce them as well.

## Input and normalization

- Only contract version `"1"` is accepted. `null`, duplicate JSON object keys,
  non-finite numbers, invalid UTF-8, and unknown object members are rejected.
- Strings are NFC-normalized and CRLF/CR line endings become LF. Location,
  artifact name/content, and path-like fields are trimmed; `file` and
  `touched_files` also convert backslashes to `/`.
- `touched_files` are canonical repo-relative paths: they have no absolute or
  drive-qualified prefix, NUL, empty component, `.` component, or `..`
  component. Duplicate touched paths are rejected after normalization. Final
  string arrays are normalized, deduplicated, and sorted by UTF-8 bytes.
  Reviewer IDs use the fixed order: correctness,
  security, performance and reliability, architecture and maintainability,
  contracts and data, adversarial, regression, test-quality.
- JCS-compatible UTF-8 bytes are used for deterministic equality and finding
  IDs. Timeout fields use JSON Schema integer semantics: finite,
  mathematically integral numbers normalize to integers; fractional and
  non-finite numbers are rejected.

## Bundle cross-fields

- `diff` is exactly `{status, content}` with `status="available"`, or exactly
  `{status, reason}` with `status="unavailable"`. The relevant string is
  non-empty and bounded.
- `artifact_presence` has exactly `tests`, `acceptance_criteria`, `migrations`,
  `logs`, and `screenshots`. An artifact field is present iff it contains at
  least one valid item; omitted and `[]` both mean absent.
- Text artifacts use `encoding="text"`; screenshots use canonical padded
  RFC 4648 base64. Names are unique within a field and decoded/content payloads
  are non-empty. Presence mismatches are
  `cross_field_invariant` at `/artifact_presence/<name>`.

## Reviewer and final results

- Reviewer results require `agent`, `summary`, `issues`, and `open_questions`.
  Issues require the fields in the reviewer schema and may contain only its
  documented optional persona fields. A routed result's `agent` must equal the
  host's expected reviewer ID.
- A final result has exactly the fields in its schema. Its completeness
  envelope has one status record per selected reviewer, in canonical order.
  Completed records omit `reason`; every other status requires a bounded
  reason. Selected reviewers are unique and no more than eight.
  `reviewed_with_no_major_issues` contains only selected reviewers whose status
  is `completed`.
- `complete` means available input, all selected reviewers completed, and no
  open questions. `blocking` describes incomplete review evidence: it means
  unavailable input, a non-completed core reviewer, or an open question. A
  completed review can still contain a merge-blocking finding; that finding
  forces `request changes` but does not make the completeness envelope
  incomplete. A non-completed optional reviewer is visible but non-blocking.
- Blockers contain actual merge-blocking findings only. `finding_id` is the
  lowercase SHA-256 of the JCS-normalized complete issue payload, excluding
  source/owner/reporting metadata. Duplicate complete payloads merge owners;
  conflicting payloads remain separate. Final blockers are sorted by severity,
  normalized location, ID, and first-owner order.

## Recommendation and host coverage

- `request changes` is required for blocking completeness, an unresolved
  question, or any blocker. Otherwise optional failure, non-blocking notes,
  follow-ups, or suggested tests require `approve with follow-ups`; only an
  entirely complete, blocker-free, follow-up-free result may be `approve`.
- The host supplies expected routed IDs/statuses, complete projected blockers,
  questions, follow-ups, and tests to `validate_final_result`. Expected status
  context always contains complete records: non-completed statuses include
  their bounded reason and completed statuses omit `reason`. The validator
  compares each supplied value after normalization and rejects omissions or
  alterations with `cross_field_invariant` at the affected final field,
  including any changed terminal reason.
- A synthesis exception, timeout, cancellation, or missing candidate is
  reported as `synthesis_failed` at the root and produces no synthetic final
  recommendation.
- The fixed unavailable-input question is exactly
  `Missing review input: diff is unavailable.` and the summary prefix is
  `Review incomplete: diff unavailable; approval is prohibited.`. Core failure
  uses `Review incomplete: core reviewer failure; approval is prohibited.`.

## Trusted capabilities and limits

Capabilities are host-owned constructor/configuration input, never bundle data
or interpolated reviewer instructions. Four boolean flags default false;
timeouts are finite, mathematically integral reviewer/synthesis values from
1–300 seconds (default 60) and aggregate deadline values from 1–900 seconds
(default 300). Required capability names are unique,
limited to the four flags, and checked in canonical order against the separate
host-owned `available_capabilities` sequence. Phase 1 has no command runner.

Before validation, enforce: 10 MiB raw bundle, 2 MiB per artifact/diff,
512 KiB per reviewer/final result, depth 16, 1,000 entries per collection,
10,000 aggregate collection entries, 50 reviewer issues/final blockers,
64 KiB ordinary strings, and 4 KiB non-completed reasons. Aggregate failures
point to the root; nested failures point to the first RFC 6901 location.
