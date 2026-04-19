from __future__ import annotations

from pathlib import PurePosixPath


SKILL_NAME = "scrutinize-me"

# The shipped payload contract is: every non-hidden file under these top-level
# entries, with REQUIRED_SKILL_FILES as the minimum subset that must exist.
SHIPPABLE_TOP_LEVEL = frozenset({"SKILL.md", "agents", "references", "evals"})

REQUIRED_SKILL_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "evals/evals.json",
    "references/reviewer-personas.md",
    "references/orchestrator-playbook.md",
    "references/review-template.md",
    "references/output-schema.md",
)


def is_shippable_relative_path(relative: PurePosixPath) -> bool:
    if not relative.parts:
        return False
    if relative.parts[0] not in SHIPPABLE_TOP_LEVEL:
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    if "__pycache__" in relative.parts or relative.suffix == ".pyc":
        return False
    return True
