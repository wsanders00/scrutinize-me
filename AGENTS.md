# Repository Guidelines

## Project Structure & Module Organization
The repository now uses a `src/` layout. Core packaging and release logic live in `src/scrutinize_me_skill/`. The skill payload itself lives in `src/scrutinize_me_skill/skill/scrutinize-me/` with `SKILL.md`, `agents/openai.yaml`, `references/`, and `evals/`. `multi-agent-code-review-template.md` is the human-readable source template for reviewer personas and orchestration rules. Keep unit tests in `tests/`, CI and release automation in `.github/workflows/`, and high-level project docs at the root.

## Build, Test, and Development Commands
Use the Python module entry points and keep generated artifacts out of version control:

- `python3 -m pip install -e .` installs the package in editable mode.
- `python3 -m unittest discover -s tests -v` runs the unit suite.
- `python3 -m scrutinize_me_skill export --target-root .agents/skills` materializes a repo-local skill for clients that discover `.agents/skills/`.
- `python3 -m scrutinize_me_skill build --output-dir dist` creates a release zip from the source skill.

Run `git diff --check` before opening a PR to catch whitespace and patch formatting issues.

## Coding Style & Naming Conventions
Use Python 3.11+ and the standard library unless an external dependency is clearly justified. Follow PEP 8 with four-space indentation, snake_case module names, and small focused functions. Skill directories must use lowercase hyphenated names to match the Agent Skills spec, for example `scrutinize-me`.

## Testing Guidelines
Write or update `unittest` coverage for every behavior change in the packaging or release flow. Mirror module responsibility in test names, for example `tests/test_builder.py` and `tests/test_skill_content.py`. When the skill content changes materially, update `src/scrutinize_me_skill/skill/scrutinize-me/evals/evals.json` so the packaged skill keeps realistic review prompts and reviewer-routing cases.

## Commit & Pull Request Guidelines
History is still shallow, so prefer short imperative subjects such as `Add release bundler` or `Refine skill prompts`. Keep PRs focused, describe the user-facing skill change, call out release-process impact, and include relevant command output when you change packaging or CI behavior.

## Contributor Notes
Do not edit generated `.agents/skills/` exports by hand; treat `src/scrutinize_me_skill/skill/scrutinize-me/` as the source of truth. Preserve strict reviewer scopes, orchestrator synthesis rules, and the “one subagent per selected persona” model when editing the skill. Keep `ai/` plans and non-essential generated docs local only; do not merge or push them unless they are actual repo or skill instructions. Releases are tag-driven: the package version in `src/scrutinize_me_skill/__init__.py` must match the pushed `vX.Y.Z` tag or the release command will fail.
