from __future__ import annotations

import re
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from scrutinize_me_skill import __version__


SKILL_NAME = "scrutinize-me"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def skill_source_dir() -> Path:
    return Path(__file__).resolve().parent / "skill" / SKILL_NAME


def validate_semver(version: str) -> str:
    if not SEMVER_PATTERN.fullmatch(version):
        raise ValueError(f"Unsupported version '{version}'. Expected SemVer, for example 1.2.3.")
    return version


def release_version_from_tag(tag: str) -> str:
    if not tag.startswith("v"):
        raise ValueError(f"Release tag '{tag}' must start with 'v'.")
    return validate_semver(tag[1:])


def ensure_tag_matches_version(tag: str, version: str) -> str:
    normalized_version = validate_semver(version)
    tag_version = release_version_from_tag(tag)
    if tag_version != normalized_version:
        raise ValueError(f"Tag {tag} does not match package version {normalized_version}.")
    return normalized_version


def materialize_skill(target_root: Path) -> Path:
    destination = target_root / SKILL_NAME
    target_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(skill_source_dir(), destination)
    return destination


def build_release_zip(output_dir: Path, version: str | None = None, release_tag: str | None = None) -> Path:
    normalized_version = validate_semver(version or __version__)
    if release_tag:
        ensure_tag_matches_version(release_tag, normalized_version)

    source_root = skill_source_dir()
    if not source_root.exists():
        raise FileNotFoundError(f"Skill source directory not found: {source_root}")

    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / f"{SKILL_NAME}-{normalized_version}.zip"

    with ZipFile(artifact, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source_root.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=f"{SKILL_NAME}/{path.relative_to(source_root)}")

    return artifact
