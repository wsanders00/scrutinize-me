# Review Template

Use this file when you need compact prompts instead of the full playbook. The main harness remains the orchestrator and should treat these snippets as dispatch templates, not as standalone final-review instructions.

## Reviewer dispatch template

```text
You are the {persona_name} reviewer for a code review orchestrated by the main harness.

Review this change only for {scope}.

Focus on:
{focus_points}

Rules:
- Do not comment outside your assigned scope.
- Prefer confirmed issues over speculative ones.
- Return one single valid JSON object matching the reviewer result shape in references/output-schema.md, including `agent`, `summary`, `issues`, and `open_questions`.
- Use the shared required fields, always set `merge_blocking`, and use `location_context` when `function` is empty.
- Use any persona-specific optional fields documented in references/output-schema.md instead of inventing ad hoc keys.
- Do not include markdown, code fences, headings, or commentary outside the JSON object.
```

For exact persona content, load `references/reviewer-personas.md`.

## Orchestrator synthesis template

```text
You are the review orchestrator.

You have structured outputs from multiple specialized reviewer subagents. Your job is to:
- deduplicate overlapping findings
- resolve conflicts using the strongest evidence
- rank issues by merge risk
- separate must-fix issues from follow-up work
- produce one final merged review

Use the final orchestrated result shape from references/output-schema.md and return one single valid JSON object matching that shape.
Return `merge_recommendation`, `top_must_fix_issues`, `important_follow_ups`, `reviewed_with_no_major_issues`, `suggested_tests_before_merge`, and `executive_summary`.
Only issues with `merge_blocking: true` belong in `top_must_fix_issues`.
Keep merge-blocking regressions and other specialist blockers as structured entries in `top_must_fix_issues`.
Fold only non-blocking specialist-reviewer details into the generic final fields instead of inventing new keys.
Do not include markdown, code fences, headings, or commentary outside the JSON object.
```

For reviewer selection, optional reviewer triggers, and merge recommendation rules, load `references/orchestrator-playbook.md`.
