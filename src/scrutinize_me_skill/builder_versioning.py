from __future__ import annotations

import re

from scrutinize_me_skill import __version__


SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


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


def normalize_build_version(version: str | None) -> str:
    package_version = validate_semver(__version__)
    if version is None:
        return package_version

    normalized_version = validate_semver(version)
    if normalized_version != package_version:
        raise ValueError(
            f"Explicit build version {normalized_version} does not match package version {package_version}."
        )
    return package_version
