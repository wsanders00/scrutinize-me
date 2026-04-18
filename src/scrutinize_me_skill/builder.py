from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from scrutinize_me_skill import __version__


SKILL_NAME = "scrutinize-me"
ALLOWED_SKILL_TOP_LEVEL = {"SKILL.md", "agents", "references", "evals"}
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def skill_source_dir() -> Path:
    return Path(__file__).resolve().parent / "skill" / SKILL_NAME


def iter_shippable_skill_files(source_root: Path | None = None) -> list[tuple[Path, str]]:
    root = (source_root or skill_source_dir()).resolve()
    shipped: list[tuple[Path, str]] = []

    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed in the shipped skill payload: {path}")
        if not path.is_file():
            continue

        rel = path.relative_to(root)
        if rel.parts[0] not in ALLOWED_SKILL_TOP_LEVEL:
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue

        shipped.append((path, rel.as_posix()))

    return shipped


def copy_shippable_skill_tree(source_root: Path, destination: Path) -> None:
    for path, relative in iter_shippable_skill_files(source_root):
        dest_path = destination / relative
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest_path)


def rename_path(source: Path, target: Path) -> None:
    source.rename(target)


def remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
    shutil.rmtree(path, ignore_errors=ignore_errors)


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


def materialize_skill(target_root: Path, *, force: bool = False) -> Path:
    source_root = skill_source_dir().resolve()
    destination = target_root / SKILL_NAME
    resolved_destination = destination.resolve(strict=False)

    try:
        resolved_destination.relative_to(source_root)
        is_within_source_root = True
    except ValueError:
        is_within_source_root = False

    if is_within_source_root:
        raise ValueError(f"Refusing to export into the source directory: {destination}")

    if destination.exists():
        if destination.is_symlink():
            raise ValueError(f"Export destination is a symlink: {destination}")
        if not destination.is_dir():
            raise ValueError(f"Export destination exists and is not a directory: {destination}")
        if not force:
            raise FileExistsError(
                f"Export destination already exists, rerun with --force: {destination}"
            )

    target_root.mkdir(parents=True, exist_ok=True)
    staging_dir = target_root / f".{SKILL_NAME}-staging-{uuid4().hex}"
    staging_dir.mkdir(parents=True, exist_ok=False)

    try:
        copy_shippable_skill_tree(source_root, staging_dir)
    except Exception:
        remove_tree(staging_dir, ignore_errors=True)
        raise

    backup_dir: Path | None = None
    try:
        if destination.exists():
            backup_dir = target_root / f".{SKILL_NAME}-backup-{uuid4().hex}"
            rename_path(destination, backup_dir)
        rename_path(staging_dir, destination)
    except Exception as exc:
        cleanup_exc: Exception | None = None
        restore_exc: Exception | None = None
        if backup_dir and backup_dir.exists():
            if destination.exists():
                try:
                    if destination.is_dir():
                        remove_tree(destination)
                    else:
                        destination.unlink()
                except Exception as err:
                    cleanup_exc = err
            try:
                backup_dir.rename(destination)
            except Exception as err:
                restore_exc = err
        extraneous_exc = restore_exc or cleanup_exc
        if extraneous_exc:
            raise exc from extraneous_exc
        raise
    else:
        if backup_dir and backup_dir.exists():
            try:
                remove_tree(backup_dir)
            except Exception:
                pass
    finally:
        if staging_dir.exists():
            try:
                remove_tree(staging_dir)
            except Exception:
                pass

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
        for path, relative in iter_shippable_skill_files(source_root):
            archive.write(path, arcname=f"{SKILL_NAME}/{relative}")

    return artifact
