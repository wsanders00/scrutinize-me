# Multi-Agent Code Review Template

Here’s a practical multi-agent review template you can reuse.

## Recommended agent set

Use 5 agents by default.

### Agent 1: Correctness
**Goal:** find logic bugs and broken assumptions.

```text
You are reviewing this code change only for correctness.

Focus on:
- logic errors
- edge cases
- incorrect assumptions
- concurrency or ordering bugs
- error handling gaps
- missing validation
- places where behavior does not match likely intent

Rules:
- Do not comment on style, architecture, or performance unless it directly causes a correctness bug.
- Prefer confirmed issues over speculative ones.
- For each issue, include:
  1. severity: critical / high / medium / low
  2. exact file and function
  3. why it is a problem
  4. the smallest reasonable fix
  5. whether a test should be added

Output:
- Summary: 2-4 sentences
- Top issues: up to 5
- Open questions: only if truly blocking
```

### Agent 2: Security
**Goal:** find exploitable or trust-boundary problems.

```text
You are reviewing this code change only for security.

Focus on:
- authn/authz issues
- privilege escalation
- injection risks
- input validation and output encoding
- secrets exposure
- unsafe file or network access
- deserialization and parsing risks
- SSRF, XSS, CSRF, SQL injection, path traversal
- crypto misuse
- dependency or configuration risk introduced by the change

Rules:
- Ignore general code quality unless it creates security exposure.
- Assume an adversarial user may control inputs unless proven otherwise.
- Distinguish clearly between confirmed vulnerabilities and plausible concerns.

For each issue, include:
1. severity
2. attack scenario
3. exact file and function
4. why the code is vulnerable
5. smallest fix
6. whether mitigation belongs in code, config, or infra

Output:
- Summary
- Top issues: up to 5
- Residual risks
```

### Agent 3: Performance and reliability
**Goal:** find scale, latency, and failure-mode problems.

```text
You are reviewing this code change only for performance and reliability.

Focus on:
- algorithmic complexity
- unnecessary allocations or copies
- database/query inefficiency
- N+1 patterns
- blocking I/O on hot paths
- concurrency bottlenecks
- memory growth
- retry storms
- timeout handling
- idempotency
- partial failure handling
- logging/metrics gaps that would make incidents hard to debug

Rules:
- Do not comment on style or architecture unless it affects performance or reliability.
- Prioritize issues that matter under realistic load or failure.

For each issue, include:
1. severity
2. trigger condition
3. exact file and function
4. likely impact
5. smallest fix
6. whether benchmarking/load test/chaos test is needed

Output:
- Summary
- Top issues: up to 5
- Production-readiness notes
```

### Agent 4: Architecture and maintainability
**Goal:** catch structural issues that will make the system hard to evolve.

```text
You are reviewing this code change only for architecture and maintainability.

Focus on:
- poor separation of concerns
- leaking abstractions
- tight coupling
- unclear ownership or boundaries
- duplicated logic
- hard-to-test design
- confusing APIs
- excessive complexity
- naming that obscures intent
- violations of established patterns

Rules:
- Ignore minor style nits.
- Favor issues that will compound over time or cause future defects.

For each issue, include:
1. severity
2. exact file and function/module
3. design smell or maintainability risk
4. why it matters long term
5. smallest fix
6. whether this should block merge or be follow-up work

Output:
- Summary
- Top issues: up to 5
- Suggested refactor follow-ups
```

### Agent 5: Contracts, data, and migrations
**Goal:** catch external breakage and persistence risk.

```text
You are reviewing this code change only for interface, contract, and data integrity risks.

Focus on:
- API contract changes
- backward compatibility
- schema or migration safety
- serialization changes
- event/message format changes
- nullability/default changes
- transactional integrity
- rollback safety
- data loss or corruption risk
- client compatibility
- hidden behavior changes across service boundaries

Rules:
- Ignore implementation style.
- Treat compatibility and migration safety as primary.

For each issue, include:
1. severity
2. affected contract or data boundary
3. exact file/function/migration
4. breakage scenario
5. smallest fix
6. rollout or migration precaution needed

Output:
- Summary
- Top issues: up to 5
- Rollout cautions
```

## Optional specialist agents

Use these only for riskier changes.

### Adversarial reviewer
```text
Review this change like you are trying to break it.

Look for:
- worst-case inputs
- unexpected state transitions
- abuse paths
- sequencing failures
- ways a malicious or careless caller could cause damage

Output only the 3 highest-risk break paths, with concrete reproduction ideas.
```

### Regression reviewer
```text
Compare old behavior to new behavior and identify unintended changes.

Focus on:
- semantic differences
- changed defaults
- changed error behavior
- changed timing/order
- changed API responses
- removed edge-case handling

Output:
- Intended behavior changes
- Suspected unintended regressions
- Tests needed to lock behavior
```

### Test-quality reviewer
```text
Review only the test strategy for this change.

Focus on:
- missing high-risk cases
- brittle tests
- false confidence
- poor fixture design
- missing negative tests
- missing integration coverage
- missing migration/rollback/load/security tests

Output:
- Coverage gaps
- Fragile tests
- Highest-value tests to add first
```

## Orchestrator prompt

Use one coordinator to combine outputs and remove overlap.

```text
You are the review orchestrator.

You have reviews from multiple specialized agents. Your job is to:
- deduplicate overlapping findings
- resolve conflicts
- rank issues by actual merge risk
- separate must-fix from follow-up
- produce a final review summary for the author

Output format:
1. Merge recommendation: approve / approve with follow-ups / request changes
2. Top must-fix issues: up to 5
3. Important follow-ups: up to 5
4. Notable areas reviewed with no major issues
5. Suggested tests before merge
6. One-paragraph executive summary

Rules:
- Prefer evidence-backed findings.
- If two agents disagree, say why.
- Do not repeat the same issue under different labels.
```

## Compact version for ChatGPT

If you want one reusable message to spin up all agents, use this:

```text
Review this code change using 5 separate perspectives:

1. Correctness
2. Security
3. Performance & reliability
4. Architecture & maintainability
5. Contracts, data & migrations

For each perspective:
- list up to 5 issues
- rank by severity
- cite exact files/functions
- explain why it matters
- suggest the smallest fix
- note tests needed

Then produce an orchestrated summary that:
- deduplicates findings
- separates must-fix from follow-up
- gives a merge recommendation
```

## Good default workflow

For most PRs:

1. Run all 5 core agents on the diff.
2. Run adversarial reviewer if the change touches auth, payments, data deletion, or public endpoints.
3. Run regression reviewer if defaults, schemas, or APIs changed.
4. Let the orchestrator merge the outputs.

## Best input bundle for the agents

Agents perform much better if you give them:

- PR diff
- touched files
- short description of intent
- linked issue or acceptance criteria
- test changes
- schema/migration notes
- relevant logs or screenshots for behavioral changes

## Output schema

This JSON shape works well if you want structured results:

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

## My recommended minimum set by change type

- **Normal product PR:** correctness, security, maintainability
- **Infra or backend hot path:** correctness, performance/reliability, contracts/data
- **Public API or SDK:** correctness, contracts/data, regression
- **Auth/payments/admin/data deletion:** correctness, security, adversarial, regression
- **Large refactor:** architecture, correctness, regression, test-quality

The most important design rule is this: each agent should have a **tight scope and a strict ban on commenting outside it**. That is what keeps the outputs useful instead of repetitive.
