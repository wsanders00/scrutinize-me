---
name: scrutinize-me
description: Use when a user asks for a code review, pull request review, merge recommendation, or severity-ranked findings across correctness, security, performance, maintainability, or contract risk.
metadata:
  scrutinize_me_version: "0.1.0"
---

# Scrutinize Me

## Overview

The main harness is the orchestrator. It owns reviewer selection, subagent dispatch, finding normalization, and the final merged review. Each reviewer subagent has one narrow scope and must not comment outside it.

## Workflow

Progress:
- [ ] Collect the full review bundle: diff, changed files, intent, tests, acceptance criteria, and migration notes.
- [ ] Load [references/orchestrator-playbook.md](references/orchestrator-playbook.md) and choose the required reviewer personas.
- [ ] Dispatch one subagent per selected persona in parallel.
- [ ] Require each subagent to return findings in the shared shape from [references/output-schema.md](references/output-schema.md).
- [ ] Deduplicate overlap, resolve conflicts, and produce one final merged review.

## Reviewer Selection

- Always run the five core reviewers: correctness, security, performance and reliability, architecture and maintainability, contracts and data.
- Add optional reviewers only when the trigger matrix in [references/orchestrator-playbook.md](references/orchestrator-playbook.md) says they are warranted.
- Keep every subagent prompt tight. Scope bans are mandatory because overlap destroys signal.

## Orchestrator Contract

- The main harness must behave as the orchestrator, not as another reviewer persona.
- Subagents generate persona-scoped findings; the orchestrator merges them into the final answer.
- Reviewer subagents and the final orchestrated result must be raw JSON matching [references/output-schema.md](references/output-schema.md), with no markdown, code fences, or commentary outside the JSON object.
- Lead with evidence-backed findings and a merge recommendation.
- Separate must-fix issues from follow-up work, and explicitly call out reviewed areas with no major issues.
- If no findings survive synthesis, say so directly and mention residual test or rollout risk.

## Progressive Disclosure

Read [references/reviewer-personas.md](references/reviewer-personas.md) for the reviewer prompts, scope bans, and output rules. Read [references/orchestrator-playbook.md](references/orchestrator-playbook.md) for reviewer selection, dispatch, and synthesis rules. Read [references/output-schema.md](references/output-schema.md) for the structured reviewer and final response formats. Read [references/review-template.md](references/review-template.md) for compact prompt templates. Read `evals/evals.json` when extending the skill and you need representative prompts for smoke testing.
