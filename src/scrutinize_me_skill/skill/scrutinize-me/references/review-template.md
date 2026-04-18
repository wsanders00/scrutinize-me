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
- Return findings using the shared schema in references/output-schema.md.
- Return exactly one single valid JSON object matching references/output-schema.md. Do not include any prose outside the JSON object; keep all prose inside the schema-defined fields.
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

Use the final response shape from references/output-schema.md and return exactly one single valid JSON object matching references/output-schema.md for the orchestrated result, with no prose outside the object.
```

For reviewer selection, optional reviewer triggers, and merge recommendation rules, load `references/orchestrator-playbook.md`.
