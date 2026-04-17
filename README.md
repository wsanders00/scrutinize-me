# scrutinize-me

`scrutinize-me` is a source-backed Agent Skills repository for orchestrated multi-agent code review. The reusable source template lives in `multi-agent-code-review-template.md`, while the distributable skill source of truth lives under `src/`. The packaged skill teaches the main harness to act as the orchestrator, dispatch one subagent per reviewer persona, and synthesize a single final review.

## Layout

- `src/scrutinize_me_skill/`: Python packaging and release tooling
- `src/scrutinize_me_skill/skill/scrutinize-me/`: the skill payload (`SKILL.md`, references, evals, agent metadata)
- `multi-agent-code-review-template.md`: source material for reviewer personas and orchestration rules
- `tests/`: unit tests for versioning, export, and bundle creation
- `.github/workflows/`: CI and tag-driven release automation

## Local development

```bash
python3 -m pip install -e .
python3 -m unittest discover -s tests -v
python3 -m scrutinize_me_skill export --target-root .agents/skills
python3 -m scrutinize_me_skill build --output-dir dist
```

When the skill is used in a subagent-capable harness, the main session should orchestrate reviewer subagents instead of doing one monolithic review pass.

## Releases

1. Update `src/scrutinize_me_skill/__init__.py` using SemVer.
2. Run the test suite locally.
3. Create an annotated tag such as `v0.1.0`.
4. Push the tag. GitHub Actions will build `dist/scrutinize-me-0.1.0.zip` and attach it to the release.
