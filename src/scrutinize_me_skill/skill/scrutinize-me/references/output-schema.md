# Output Schema

Use these shapes to normalize subagent output before the orchestrator merges findings.

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
      "confidence": "high",
      "why_it_matters": "Any authenticated user can trigger an admin-only action.",
      "smallest_fix": "Add role check before executing deletion.",
      "test_needed": "Integration test for non-admin access denial"
    }
  ],
  "open_questions": []
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
- Every issue must include severity, location, impact, and the smallest fix.
- Use `top_must_fix_issues` only for blockers.
- Put speculative or lower-priority work in `important_follow_ups`.
- If no blockers remain, the orchestrator may return `approve` or `approve with follow-ups`.
- If no findings survive synthesis, return empty issue lists and explain residual risk in `executive_summary`.
