# Orchestrator Playbook

The main harness is the orchestrator. It selects reviewer personas, dispatches one subagent per selected persona, and merges the outputs into a single final review.

## Required input bundle

Provide reviewers with as much of this bundle as possible:

- PR diff or patch
- touched files
- short description of intent
- linked issue or acceptance criteria
- test changes
- schema or migration notes
- logs or screenshots for behavior changes

## Default workflow

1. Collect the review bundle.
2. Run the five core reviewers in parallel.
3. Add optional specialist reviewers only when their trigger conditions are met.
4. Require all subagents to return the reviewer result shape from `references/output-schema.md`.
5. Merge duplicate findings, resolve conflicts, and rank by actual merge risk.
6. Produce one final review with a merge recommendation.

## Optional reviewer trigger matrix

- Add the adversarial reviewer when the change touches auth, payments, admin actions, data deletion, or public endpoints.
- Add the regression reviewer when defaults, schemas, APIs, or serialized message formats change.
- Add the test-quality reviewer when the change is large, test-heavy, or high-risk without obvious coverage.

## Merge recommendation categories

- `approve`: no `merge_blocking: true` findings remain and no notable follow-up work remains
- `approve with follow-ups`: no `merge_blocking: true` findings remain, but notable risks, cleanup work, or rollout cautions remain
- `request changes`: at least one `merge_blocking: true` finding remains after synthesis

## Reviewer result contract

- Every reviewer must use the reviewer result shape from `references/output-schema.md`, not the final orchestrated result shape.
- Every reviewer issue must include `severity`, `confidence`, and an explicit `merge_blocking` boolean.
- Use `severity` only to rank risk. Do not infer blockers from severity alone.
- When there is no sensible callable or symbol name, reviewers should set `function` to `""` and use `location_context` to point at the exact config key, schema field, prompt section, or document fragment.

## Rating guidance

- `severity` must be one of `critical`, `high`, `medium`, or `low`.
- `confidence` must be one of `high`, `medium`, or `low`.
- Set `merge_blocking` to `true` only when shipping the change as-is would make merge unsafe because of likely correctness breakage, security exposure, contract breakage, data loss or corruption, or serious operability risk.
- Set `merge_blocking` to `false` for speculative concerns, follow-up refactors, or issues that are real but safe to defer.

## Deduplication and conflict resolution

- Merge overlapping findings under the most precise label.
- Prefer the strongest evidence and the smallest viable fix.
- If two reviewers disagree, explain why and call out the uncertainty explicitly.
- Only issues with `merge_blocking: true` belong in `top_must_fix_issues`.
- Preserve `location_context` in the merged result when it adds precision beyond `file` and `function`.
- Do not repeat the same issue under multiple categories.
- Keep style commentary out unless it creates a correctness, security, performance, or maintainability risk.

## Specialist reviewer folding rules

- Fold adversarial reproduction detail into the merged finding narrative; do not invent a separate final-response key for it.
- If a regression or other specialist reviewer marks an issue `merge_blocking: true`, preserve it as a structured blocker in `top_must_fix_issues`.
- Fold only non-blocking intended behavior changes, suspected regressions, and test-quality outputs into `important_follow_ups`, `suggested_tests_before_merge`, or `executive_summary`.

## Final synthesis rules

- Lead with the merge recommendation.
- Separate must-fix issues from important follow-ups.
- Include notable areas reviewed with no major issues.
- Suggest the highest-value tests before merge.
- End with a short executive summary, not a changelog.
