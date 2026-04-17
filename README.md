# scrutinize-me

This repository packages and releases the `scrutinize-me` Agent Skill for orchestrated, multi-agent code review.

The key idea is that the *main harness* is the **orchestrator**: it selects reviewer personas, dispatches **one subagent per persona** (in parallel), requires a shared structured output shape, then synthesizes a single final review with a merge recommendation.

## Repository Map

- `src/scrutinize_me_skill/`: Python packaging, CLI entry points, and release bundling logic
- `src/scrutinize_me_skill/skill/scrutinize-me/`: shipped skill payload, including `SKILL.md`, harness metadata, references, and evals
- `tests/`: unit coverage for export, build, and release/version invariants
- `.github/workflows/`: CI and tag-driven release automation

## What The Skill Does

The skill content lives at `src/scrutinize_me_skill/skill/scrutinize-me/` and is meant to be consumed by a subagent-capable harness.

At a high level:

- The orchestrator runs the **five core reviewers** by default: correctness, security, performance and reliability, architecture and maintainability, contracts and data.
- Optional specialists (for example adversarial, regression, test-quality) are added only when the trigger matrix says they are warranted.
- Subagents return findings in a shared JSON schema (`references/output-schema.md`), and the orchestrator deduplicates and resolves conflicts.

If your harness supports it, the skill is designed to be invoked as `$scrutinize-me` (see `agents/openai.yaml`).

## Skill Source Of Truth

`src/scrutinize_me_skill/skill/scrutinize-me/` is the only source of truth for what gets exported, bundled, and shipped as the skill.

The packaged skill’s operational docs are under:

- `src/scrutinize_me_skill/skill/scrutinize-me/references/`: orchestrator playbook, persona prompts, schemas, compact templates
- `src/scrutinize_me_skill/skill/scrutinize-me/evals/evals.json`: representative prompts/checks for smoke testing reviewer routing and synthesis behavior

When you update reviewer personas, orchestration rules, or compact prompt templates, edit the maintained files under `src/scrutinize_me_skill/skill/scrutinize-me/references/` and keep them aligned with `SKILL.md`.

## Install And Use Locally

This project is intentionally lightweight (standard library only). The Python package provides a CLI to export the skill into a discoverable directory and to build a release zip.

### 1) Install the tooling (editable)

```bash
python3 -m pip install -e .
```

The package requires Python 3.11 or newer.

### 2) Export the skill to a harness-discoverable directory

Export copies the skill payload into `.agents/skills/scrutinize-me/` by default.

```bash
python3 -m scrutinize_me_skill export --target-root .agents/skills
```

Note: export replaces the destination directory if it already exists.

### 3) Invoke in your harness

How you run a skill depends on your harness, but the expected flow is:

- Ensure the harness can discover skills under `.agents/skills/`
- Provide the orchestrator with a review bundle (diff/changed files/intent/tests/etc.)
- Ask the main session to use `$scrutinize-me` and dispatch persona subagents (the skill documents the exact workflow in `SKILL.md` and `references/`)

Example prompt:

```text
Use $scrutinize-me to review this pull request. You are the orchestrator. Run the default reviewer personas in parallel, add any required specialist reviewers, then return one merged review with a merge recommendation.
```

## CLI Reference

After installation you can use either the module entry point or the console script:

```bash
# Module entry point
python3 -m scrutinize_me_skill version
python3 -m scrutinize_me_skill export --target-root .agents/skills
python3 -m scrutinize_me_skill build --output-dir dist

# Console script (installed by the package)
scrutinize-me version
scrutinize-me export --target-root .agents/skills
scrutinize-me build --output-dir dist
```

`build` produces a versioned artifact: `dist/scrutinize-me-<version>.zip`.

## Tests

Run the unit suite:

```bash
python3 -m unittest discover -s tests -v
```

The tests validate:

- version/tag SemVer checks for releases
- `export` materializes a correctly shaped Agent Skill directory
- `build` zips the full skill payload (including `agents/`, `references/`, and `evals/`)

## Releases

Releases are tag-driven via GitHub Actions (`.github/workflows/release.yml`):

1. Update `src/scrutinize_me_skill/__init__.py` (`__version__`) using SemVer.
2. Run `python3 -m unittest discover -s tests -v`.
3. Create and push a tag like `v0.1.0` (must match `__version__`).
4. CI builds `dist/*.zip` and attaches it to the GitHub Release.

## Files That Matter Most

- `src/scrutinize_me_skill/skill/scrutinize-me/SKILL.md`: the orchestrator contract and top-level workflow.
- `src/scrutinize_me_skill/skill/scrutinize-me/references/orchestrator-playbook.md`: reviewer selection, triggers, synthesis rules.
- `src/scrutinize_me_skill/skill/scrutinize-me/references/reviewer-personas.md`: persona prompts and scope bans.
- `src/scrutinize_me_skill/skill/scrutinize-me/references/output-schema.md`: required structured output shapes.
- `src/scrutinize_me_skill/skill/scrutinize-me/references/review-template.md`: compact reviewer dispatch and synthesis prompt templates.
- `src/scrutinize_me_skill/skill/scrutinize-me/agents/openai.yaml`: harness-facing metadata (display name, default prompt, implicit invocation policy).
- `src/scrutinize_me_skill/builder.py`: export and release zip implementation.
- `tests/`: validates the bundle/export/release invariants.
