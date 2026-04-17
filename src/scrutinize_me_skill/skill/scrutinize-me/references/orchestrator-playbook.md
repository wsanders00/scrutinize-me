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
4. Require all subagents to return findings in the shared shape from `references/output-schema.md`.
5. Merge duplicate findings, resolve conflicts, and rank by actual merge risk.
6. Produce one final review with a merge recommendation.

## Optional reviewer trigger matrix

- Add the adversarial reviewer when the change touches auth, payments, admin actions, data deletion, or public endpoints.
- Add the regression reviewer when defaults, schemas, APIs, or serialized message formats change.
- Add the test-quality reviewer when the change is large, test-heavy, or high-risk without obvious coverage.

## Merge recommendation categories

- `approve`: no must-fix findings remain
- `approve with follow-ups`: merge is acceptable, but notable risks or cleanup work remain
- `request changes`: at least one finding is severe enough to block merge

## Deduplication and conflict resolution

- Merge overlapping findings under the most precise label.
- Prefer the strongest evidence and the smallest viable fix.
- If two reviewers disagree, explain why and call out the uncertainty explicitly.
- Do not repeat the same issue under multiple categories.
- Keep style commentary out unless it creates a correctness, security, performance, or maintainability risk.

## Final synthesis rules

- Lead with the merge recommendation.
- Separate must-fix issues from important follow-ups.
- Include notable areas reviewed with no major issues.
- Suggest the highest-value tests before merge.
- End with a short executive summary, not a changelog.
