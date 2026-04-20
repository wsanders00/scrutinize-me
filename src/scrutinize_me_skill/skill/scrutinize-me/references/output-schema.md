# Output Schema

Use these shapes to normalize subagent output before the orchestrator merges findings.

## Shared issue conventions

- `severity` must be one of `critical`, `high`, `medium`, or `low`.
- `confidence` must be one of `high`, `medium`, or `low`.
- `merge_blocking` is required on every reviewer issue. Set it to `true` only when the issue should block merge.
- `file` should be the repo-relative path or best-known artifact path under review.
- `function` should name the callable, route, symbol, migration, test, or prompt section when one exists. If there is no sensible symbol, set `function` to `""` and use `location_context` to pinpoint the exact config key, schema field, JSON path, YAML stanza, or document fragment.

## Reviewer result required top-level keys

- `agent`: stable reviewer identifier. Use one of `correctness`, `security`, `performance and reliability`, `architecture and maintainability`, `contracts and data`, `adversarial`, `regression`, or `test-quality`.
- `summary`: short reviewer-lane summary grounded in the current change.
- `issues`: array of persona-scoped findings. Use `[]` when no issues survive review.
- `open_questions`: blocking uncertainties only. Use `[]` when none remain.
- Include persona-specific top-level arrays only when the assigned lane needs them.

## Final orchestrated result required top-level keys

- `merge_recommendation`: one of `approve`, `approve with follow-ups`, or `request changes`.
- `top_must_fix_issues`: structured blocker findings only. Each item must include `severity`, `title`, `owners`, `file`, `function`, `why_it_matters`, and `smallest_fix`; include `location_context` when it adds precision.
- `important_follow_ups`: non-blocking but meaningful issues or rollout notes that should not be forgotten.
- `reviewed_with_no_major_issues`: reviewer lanes that were examined and produced no surviving major findings.
- `suggested_tests_before_merge`: highest-value remaining tests to add before merge.
- `executive_summary`: short synthesis of the actual merge posture after deduplication.

## Reviewer result

```json
{
  "agent": "security",
  "summary": "Found 2 high-risk issues and 1 medium-risk concern.",
  "issues": [
    {
      "severity": "high",
      "title": "Authorization check missing on admin action",
      "file": "api/admin.py",
      "function": "delete_user",
      "location_context": "POST /admin/users/{id}",
      "confidence": "high",
      "why_it_matters": "Any authenticated user can trigger an admin-only action.",
      "smallest_fix": "Add role check before executing deletion.",
      "test_needed": "Integration test for non-admin access denial",
      "merge_blocking": true,
      "attack_scenario": "A non-admin caller invokes the admin endpoint directly.",
      "mitigation_scope": "code",
      "trigger_condition": "",
      "likely_impact": "",
      "affected_contract": "",
      "breakage_scenario": "",
      "rollout_caution": "",
      "reproduction_idea": ""
    }
  ],
  "open_questions": [],
  "residual_risks": [],
  "production_readiness_notes": [],
  "suggested_refactor_follow_ups": [],
  "rollout_cautions": [],
  "intended_behavior_changes": [],
  "suspected_unintended_regressions": [],
  "coverage_gaps": [],
  "fragile_tests": [],
  "highest_value_tests_to_add_first": []
}
```

## Final orchestrated result

```json
{
  "merge_recommendation": "request changes",
  "top_must_fix_issues": [
    {
      "severity": "high",
      "title": "Authorization check missing on admin action",
      "owners": ["security", "correctness"],
      "file": "api/admin.py",
      "function": "delete_user",
      "location_context": "POST /admin/users/{id}",
      "why_it_matters": "Any authenticated user can trigger an admin-only action.",
      "smallest_fix": "Add role check before executing deletion."
    }
  ],
  "important_follow_ups": [],
  "reviewed_with_no_major_issues": [
    "performance and reliability"
  ],
  "suggested_tests_before_merge": [
    "Integration test for non-admin access denial"
  ],
  "executive_summary": "One merge-blocking authorization bug remains. Other reviewed areas did not surface additional major issues."
}
```

## Response rules

- Return raw JSON only.
- Do not wrap the response in Markdown code fences.
- Do not include headings, commentary, or any text outside the JSON object.
- Every reviewer result must include `agent`, `summary`, `issues`, and `open_questions`.
- Every reviewer issue must include `severity`, `title`, `file`, `function`, `confidence`, `why_it_matters`, `smallest_fix`, `test_needed`, and `merge_blocking`.
- Use `function` for the nearest callable or named symbol. If none exists, set `function` to `""` and use `location_context`.
- Use the optional issue fields when the assigned persona requires them: `location_context`, `attack_scenario`, `mitigation_scope`, `trigger_condition`, `likely_impact`, `affected_contract`, `breakage_scenario`, `rollout_caution`, and `reproduction_idea`.
- Use the optional top-level arrays when the assigned persona requires them: `residual_risks`, `production_readiness_notes`, `suggested_refactor_follow_ups`, `rollout_cautions`, `intended_behavior_changes`, `suspected_unintended_regressions`, `coverage_gaps`, `fragile_tests`, and `highest_value_tests_to_add_first`.
- Every final orchestrated result must include `merge_recommendation`, `top_must_fix_issues`, `important_follow_ups`, `reviewed_with_no_major_issues`, `suggested_tests_before_merge`, and `executive_summary`.
- Every final blocker item must include `severity`, `title`, `owners`, `file`, `function`, `why_it_matters`, and `smallest_fix`.
- `owners` must list the reviewer identifiers that support the merged finding.
- Use `top_must_fix_issues` only for blockers.
- Put speculative or lower-priority work in `important_follow_ups`.
- The final orchestrated result intentionally uses generic fields. Fold only non-blocking specialist-reviewer detail into `important_follow_ups`, `suggested_tests_before_merge`, and `executive_summary` instead of inventing new final-response keys.
- Blocking specialist findings, including regressions, stay as structured issues in `top_must_fix_issues`.
- Final merged blocker items may include `location_context` when `function` is empty or too coarse.
- If no blockers remain, the orchestrator may return `approve` or `approve with follow-ups`.
- If no findings survive synthesis, return empty issue lists and explain residual risk in `executive_summary`.
