---
name: scrutinize-me
description: Use only for final, highly critical code reviews, strict merge-risk assessments, security/correctness-heavy reviews, or when the user explicitly requests a very strict multi-reviewer review with severity-ranked findings.
metadata:
  scrutinize_me_version: "0.3.1"
---

# Scrutinize Me

## Overview

Use this skill sparingly. It is a strict final-review harness, not the normal checkpoint review path. For routine handoff, pre-merge, or broad checkpoint review, prefer `requesting-code-review` unless the work is high criticality or the user explicitly asks for this strict review.

The main harness is the orchestrator. It owns reviewer selection, subagent dispatch, finding normalization, and the final merged review. Each reviewer subagent has one narrow scope and must not comment outside it.

## Workflow

Progress:
- [ ] Collect the full review bundle: diff, changed files, intent, tests, acceptance criteria, and migration notes.
- [ ] Run a preflight on the review bundle to verify present artifacts and identify missing artifacts.
- [ ] Ask for missing critical artifacts before dispatch when they block reliable review.
- [ ] Load [references/orchestrator-playbook.md](references/orchestrator-playbook.md) and choose the required reviewer personas.
- [ ] Dispatch one subagent per selected persona in parallel.
- [ ] Require each subagent to return the reviewer result shape from [references/output-schema.md](references/output-schema.md).
- [ ] Deduplicate overlap, resolve conflicts, and produce one final merged review.

## Reviewer Selection

- Always run the five core reviewers: correctness, security, performance and reliability, architecture and maintainability, contracts and data.
- Add optional reviewers only when the trigger matrix in [references/orchestrator-playbook.md](references/orchestrator-playbook.md) says they are warranted.
- Keep every subagent prompt tight. Scope bans are mandatory because overlap destroys signal.

## Orchestrator Contract

- The main harness must behave as the orchestrator, not as another reviewer persona.
- Subagents generate persona-scoped findings; the orchestrator merges them into the final answer.
- Reviewer subagents must return the reviewer result shape from [references/output-schema.md](references/output-schema.md), and the final answer must return the final orchestrated result shape from the same file.
- Reviewer subagents and the final orchestrated result must be raw JSON only, with no markdown, code fences, or commentary outside the JSON object.
- Every reviewer issue must carry an explicit `merge_blocking` decision so the orchestrator does not guess blockers from severity alone.
- Lead with evidence-backed findings and a merge recommendation.
- Separate must-fix issues from follow-up work, and explicitly call out reviewed areas with no major issues.
- If required context is incomplete, capture unresolved missing-artifact limits in final `open_questions`; reflect merge-impacting uncertainty in `executive_summary`.
- If no findings survive synthesis, say so directly and mention residual test or rollout risk.

## Progressive Disclosure

Read [references/reviewer-personas.md](references/reviewer-personas.md) for the reviewer prompts, scope bans, and output rules. Read [references/orchestrator-playbook.md](references/orchestrator-playbook.md) for reviewer selection, dispatch, and synthesis rules. Read [references/output-schema.md](references/output-schema.md) for the structured reviewer and final response formats. Read [references/review-template.md](references/review-template.md) for compact prompt templates. Read `evals/evals.json` when extending the skill and you need representative prompts for smoke testing.
