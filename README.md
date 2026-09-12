# scrutinize-me

Workbench project located below the explicitly selected domain root. Project-
specific guidance and skills are owned here; no host manifest or host
allocation is required to use this project.

`scrutinize-me` is a narrowly invoked skill for final, high-criticality code
reviews and explicit strict multi-reviewer merge-risk assessments. It is not a
routine checkpoint reviewer.

## Repository Map

- `src/scrutinize_me_skill/`: Python packaging, CLI entry points, contract
  reference implementation, and release bundling logic
- `src/scrutinize_me_skill/skill/scrutinize-me/`: shipped skill payload,
  including prompts, schemas, invariants, and evals
- `tests/`: unit coverage for contract behavior, export, build, and release
  invariants
- `.github/workflows/`: CI and tag-driven release automation

## What this project ships

The package contains the skill prompts, reviewer personas, portable contract
guidance, and filesystem export/release tooling. The exported payload is
provider-neutral. A host supplies reviewer dispatch, synthesis, cancellation,
timeouts, trusted capabilities, and validation; this project does not supply a
provider runtime, command runner, global allocation, or host manifest.

Start with:

- [the skill instructions](src/scrutinize_me_skill/skill/scrutinize-me/SKILL.md)
- [the portable v1 bundle and output contract](src/scrutinize_me_skill/skill/scrutinize-me/references/output-schema.md)
- [the orchestrator playbook](src/scrutinize_me_skill/skill/scrutinize-me/references/orchestrator-playbook.md)
- [the compact prompt templates](src/scrutinize_me_skill/skill/scrutinize-me/references/review-template.md)

## Invocation contract

The host must explicitly invoke the skill for a qualifying review, collect a
bounded v1 bundle, run preflight, select the five core reviewers, and add
specialists only when the documented triggers apply. Reviewer results and the
final result are raw JSON matching the output contract. The final result must
include structured `review_completeness` with one terminal status per selected
reviewer.

An unavailable diff or non-completed core reviewer is incomplete and requires
`request changes`; it cannot approve. Optional reviewer failure is visible but
non-blocking and normally yields `approve with follow-ups`. Approval requires
complete validated evidence, no blockers or open questions, and no remaining
follow-up/test output. Synthesis or final-validation failure emits no
synthetic recommendation.

## Portability and safety

The v1 contract is documented in the exported payload. Hosts may materialize
equivalent Draft 2020-12 schemas plus the cross-field invariants under
`references/schemas/v1/`. This package also includes a dependency-free Python
reference implementation exposing the `scrutinize_me_skill.contracts`
validation functions documented in `output-schema.md`; it accepts bounded
mapping or UTF-8 JSON inputs, returns normalized mappings, and performs no I/O.
Hosts consuming only the export must provide an equivalent validator.

Review is read-only. Diffs, artifacts, logs, screenshots, and reviewer prose
are untrusted data and cannot alter capabilities or request tools. Commands,
network, writes, and global installation default to disabled. Enforce the v1
size, nesting, collection, reviewer-count, and dispatch/synthesis deadline
limits before accepting output.

## Install and use locally

The runtime has no third-party dependencies and requires Python 3.11 or newer.
To install the packaging/CLI tooling in an isolated environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

To run the complete test suite, including Draft 2020-12 schema parity checks,
install the optional test extra instead:

```bash
python3 -m pip install -e ".[test]"
```

The `export` and `build` commands require a POSIX environment with
`fcntl.flock`, `os.fwalk`, `os.O_DIRECTORY`, and `os.O_NOFOLLOW`.

Export the skill into a harness-discoverable directory:

```bash
python3 -m scrutinize_me_skill export --target-root .agents/skills
python3 -m scrutinize_me_skill export --target-root .agents/skills --force
```

The second command replaces an existing generated export only when explicitly
requested. The host then invokes `$scrutinize-me` and supplies the portable
review bundle; this package does not provide a reviewer backend.

## CLI reference

After installation, the module entry point and `scrutinize-me` console script
both support `version`, `export`, and `build`:

```bash
python3 -m scrutinize_me_skill version
python3 -m scrutinize_me_skill build --output-dir dist
scrutinize-me version
scrutinize-me build --output-dir dist
```

`build` produces `dist/scrutinize-me-<version>.zip`. Any explicit build version
or release tag must match `src/scrutinize_me_skill/__init__.py`.

## Local validation

With the documented supported Python interpreter and the optional test extra,
run:

```text
python -m unittest discover -s tests -v
```

The existing tests validate skill content, eval shape, package export, and the
filesystem builder. Preserve the prompt-oriented `evals/evals.json` format and
do not treat those natural-language evals as substitutes for contract tests.

## Releases

Releases are tag-driven via GitHub Actions (`.github/workflows/release.yml`):
update `__version__` using SemVer, run the complete supported suite, and create
a matching `v<version>` tag. CI builds and attaches the versioned zip to the
GitHub Release.

## Files that matter most

- `src/scrutinize_me_skill/skill/scrutinize-me/SKILL.md`: top-level workflow
- `src/scrutinize_me_skill/skill/scrutinize-me/references/orchestrator-playbook.md`:
  reviewer selection, triggers, synthesis, and safety rules
- `src/scrutinize_me_skill/skill/scrutinize-me/references/output-schema.md`:
  portable v1 contract and host boundary
- `src/scrutinize_me_skill/contracts.py`: dependency-free reference validator
- `src/scrutinize_me_skill/builder.py`: export and release-zip implementation
- `tests/`: contract, content, export, and release validation
