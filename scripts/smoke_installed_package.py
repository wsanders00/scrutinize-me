#!/usr/bin/env python3
from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

from scrutinize_me_skill.manifest import REQUIRED_SKILL_FILES, SKILL_NAME


DIST_NAME = "scrutinize-me-skill"
EXPECTED_RELATIVE_FILES = REQUIRED_SKILL_FILES
COMMAND_TIMEOUT_SECONDS = 30
COMMAND_TIMEOUT_ENVVAR = "SCRUTINIZE_ME_SMOKE_TIMEOUT_SECONDS"


def command_timeout_seconds() -> int:
    raw_value = os.environ.get(COMMAND_TIMEOUT_ENVVAR)
    if raw_value is None or not raw_value.strip():
        return COMMAND_TIMEOUT_SECONDS

    try:
        timeout_seconds = int(raw_value)
    except ValueError as exc:
        raise AssertionError(
            f"{COMMAND_TIMEOUT_ENVVAR} must be a positive integer, got {raw_value!r}."
        ) from exc

    if timeout_seconds <= 0:
        raise AssertionError(
            f"{COMMAND_TIMEOUT_ENVVAR} must be a positive integer, got {raw_value!r}."
        )
    return timeout_seconds


def _normalize_timeout_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def run_command(args: list[str]) -> str:
    timeout_seconds = command_timeout_seconds()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _normalize_timeout_output(
            exc.output if exc.output is not None else getattr(exc, "stdout", None)
        )
        stderr = _normalize_timeout_output(
            exc.stderr if exc.stderr is not None else getattr(exc, "stderr", None)
        )
        raise AssertionError(
            f"Command timed out after {timeout_seconds}s: {' '.join(args)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        ) from exc
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
    console_script = Path(sys.executable).with_name(SKILL_NAME)
    if not console_script.is_file():
        raise AssertionError(f"Expected console script is missing: {console_script}")
    console_version = run_command([str(console_script), "version"])
    if console_version != installed_version:
        raise AssertionError(
            "Installed console script version mismatch: "
            f"metadata={installed_version!r}, console={console_version!r}"
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
