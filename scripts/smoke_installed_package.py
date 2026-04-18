#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile


SKILL_NAME = "scrutinize-me"
DIST_NAME = "scrutinize-me-skill"
EXPECTED_RELATIVE_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "evals/evals.json",
    "references/orchestrator-playbook.md",
    "references/output-schema.md",
    "references/review-template.md",
    "references/reviewer-personas.md",
)


def run_command(args: list[str]) -> str:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        raise AssertionError(
            f"Command failed ({completed.returncode}): {' '.join(args)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )
    return completed.stdout.strip()


def assert_expected_skill_files(skill_root: Path) -> None:
    for relative in EXPECTED_RELATIVE_FILES:
        target = skill_root / relative
        if not target.is_file():
            raise AssertionError(f"Expected file is missing: {target}")


def assert_expected_archive_files(artifact: Path) -> None:
    with ZipFile(artifact) as archive:
        names = set(archive.namelist())
    for relative in EXPECTED_RELATIVE_FILES:
        archived = f"{SKILL_NAME}/{relative}"
        if archived not in names:
            raise AssertionError(f"Expected archive member is missing: {archived}")


def main() -> int:
    installed_version = importlib.metadata.version(DIST_NAME)
    reported_version = run_command([sys.executable, "-m", "scrutinize_me_skill", "version"])
    if reported_version != installed_version:
        raise AssertionError(
            "Installed distribution version mismatch: "
            f"metadata={installed_version!r}, cli={reported_version!r}"
        )

    with tempfile.TemporaryDirectory(prefix="scrutinize-me-smoke-") as tmp:
        root = Path(tmp)
        export_root = root / "exported"
        build_root = root / "build"

        run_command(
            [
                sys.executable,
                "-m",
                "scrutinize_me_skill",
                "export",
                "--target-root",
                str(export_root),
            ]
        )
        exported_skill = export_root / SKILL_NAME
        if not exported_skill.is_dir():
            raise AssertionError(f"Expected exported directory is missing: {exported_skill}")
        assert_expected_skill_files(exported_skill)

        run_command(
            [
                sys.executable,
                "-m",
                "scrutinize_me_skill",
                "build",
                "--output-dir",
                str(build_root),
            ]
        )
        artifact = build_root / f"{SKILL_NAME}-{installed_version}.zip"
        if not artifact.is_file():
            raise AssertionError(f"Expected build artifact is missing: {artifact}")
        assert_expected_archive_files(artifact)

    print("installed-package smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
