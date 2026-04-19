from __future__ import annotations

from contextlib import contextmanager
import os
import stat
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from scrutinize_me_skill import builder_platform as _builder_platform
from scrutinize_me_skill import builder_state as _builder_state
from scrutinize_me_skill import builder_transfer as _builder_transfer
from scrutinize_me_skill.builder_fs import (
    entry_exists,
    ensured_directory,
    held_export_lock,
    lstat_entry,
    opened_directory,
    opened_directory_at,
    opened_parent_directory,
    remove_entry_at,
    rename_entry,
    replace_entry,
    sync_tree_at,
    write_all,
)
from scrutinize_me_skill.builder_platform import (
    find_symlink_component,
    trusted_symlink_scan_base,
    validate_not_within_source,
    validate_write_path,
)
from scrutinize_me_skill.builder_state import (
    ExportStateInvalidError,
    ExportStateOversizeError,
    export_state_name,
    export_state_path,
    validated_export_state_name,
)
from scrutinize_me_skill.builder_transfer import (
    ShippableFileSnapshot,
    iter_shippable_skill_files,
    skill_source_dir,
    validate_skill_source,
)
from scrutinize_me_skill.builder_versioning import (
    ensure_tag_matches_version,
    normalize_build_version,
    release_version_from_tag,
    validate_semver,
)
from scrutinize_me_skill.manifest import SKILL_NAME


NOFOLLOW_FLAG = _builder_platform.NOFOLLOW_FLAG
DIRECTORY_FLAG = _builder_platform.DIRECTORY_FLAG
fcntl = _builder_platform.fcntl
EXPORT_STATE_MAX_BYTES = _builder_state.EXPORT_STATE_MAX_BYTES


def ensure_supported_platform() -> None:
    _builder_platform.ensure_supported_platform(
        os_module=os,
        fcntl_module=fcntl,
        directory_flag=DIRECTORY_FLAG,
        nofollow_flag=NOFOLLOW_FLAG,
    )


def read_export_state_bytes(root_fd: int, state_name: str, state_path: Path) -> bytes:
    return _builder_state.read_export_state_bytes(
        root_fd,
        state_name,
        state_path,
        nofollow_flag=NOFOLLOW_FLAG,
        os_module=os,
    )


def snapshot_shippable_skill_files(source_root: Path | None = None) -> list[ShippableFileSnapshot]:
    return _builder_transfer.snapshot_shippable_skill_files(
        source_root,
        iter_files=iter_shippable_skill_files,
    )


@contextmanager
def opened_snapshotted_file(snapshot: ShippableFileSnapshot):
    with _builder_transfer.opened_snapshotted_file(
        snapshot,
        nofollow_flag=NOFOLLOW_FLAG,
    ) as fd:
        yield fd


def stream_fd_to_fd(source_fd: int, destination_fd: int, *, label: str) -> None:
    _builder_transfer.stream_fd_to_fd(
        source_fd,
        destination_fd,
        label=label,
        write_chunk=write_all,
    )


def stream_fd_to_writer(source_fd: int, writer, *, label: str) -> None:
    _builder_transfer.stream_fd_to_writer(source_fd, writer, label=label)


def copy_shippable_skill_tree(
    source_root: Path,
    destination: Path,
    *,
    destination_fd: int | None = None,
) -> None:
    _builder_transfer.copy_shippable_skill_tree(
        source_root,
        destination,
        destination_fd=destination_fd,
        snapshot_files=snapshot_shippable_skill_files,
        open_snapshot=opened_snapshotted_file,
        open_parent=opened_parent_directory,
        ensure_directory=ensured_directory,
        stream_to_fd=stream_fd_to_fd,
        nofollow_flag=NOFOLLOW_FLAG,
    )


def write_export_state(root_fd: int, target_root: Path, *, staging_name: str, backup_name: str | None) -> None:
    _builder_state.write_export_state(
        root_fd,
        target_root,
        staging_name=staging_name,
        backup_name=backup_name,
        replace=replace_entry,
        remove=remove_entry_at,
        id_factory=uuid4,
        nofollow_flag=NOFOLLOW_FLAG,
        os_module=os,
    )


def clear_export_state(root_fd: int) -> None:
    _builder_state.clear_export_state(root_fd, remove=remove_entry_at)


def quarantine_export_state(root_fd: int) -> None:
    _builder_state.quarantine_export_state(
        root_fd,
        replace=replace_entry,
        id_factory=uuid4,
        os_module=os,
    )


def recover_export_state(target_root: Path, root_fd: int, destination_name: str) -> None:
    _builder_state.recover_export_state(
        target_root,
        root_fd,
        destination_name,
        read_bytes=read_export_state_bytes,
        quarantine=quarantine_export_state,
        clear=clear_export_state,
    )


def materialize_skill(target_root: Path, *, force: bool = False) -> Path:
    ensure_supported_platform()
    source_root = validate_skill_source(skill_source_dir())
    destination = target_root / SKILL_NAME

    validate_write_path(destination, label="Export destination")
    resolved_destination = destination.resolve(strict=False)

    try:
        resolved_destination.relative_to(source_root)
        is_within_source_root = True
    except ValueError:
        is_within_source_root = False

    if is_within_source_root:
        raise ValueError(f"Refusing to export into the source directory: {destination}")

    with ensured_directory(target_root, label="Export target root") as root_fd:
        with held_export_lock(root_fd):
            recover_export_state(target_root, root_fd, SKILL_NAME)

            destination_info = lstat_entry(root_fd, SKILL_NAME)
            if destination_info is not None:
                if not stat.S_ISDIR(destination_info.st_mode):
                    raise ValueError(f"Export destination exists and is not a directory: {destination}")
                if not force:
                    raise FileExistsError(
                        f"Export destination already exists, rerun with --force: {destination}"
                    )

            staging_name = f".{SKILL_NAME}-staging-{uuid4().hex}"
            staging_dir = target_root / staging_name
            os.mkdir(staging_name, 0o700, dir_fd=root_fd)
            backup_name = (
                f".{SKILL_NAME}-backup-{uuid4().hex}" if destination_info is not None else None
            )

            try:
                with opened_directory_at(
                    root_fd,
                    staging_name,
                    label="Export staging directory",
                ) as staging_fd:
                    copy_shippable_skill_tree(
                        source_root,
                        staging_dir,
                        destination_fd=staging_fd,
                    )
                sync_tree_at(root_fd, staging_name)
            except BaseException:
                remove_entry_at(root_fd, staging_name, ignore_errors=True)
                raise

            commit_published = False
            try:
                write_export_state(
                    root_fd,
                    target_root,
                    staging_name=staging_name,
                    backup_name=backup_name,
                )
                if backup_name is not None:
                    rename_entry(root_fd, SKILL_NAME, backup_name)
                    os.fsync(root_fd)
                rename_entry(root_fd, staging_name, SKILL_NAME)
                os.fsync(root_fd)
                commit_published = True
            except BaseException as exc:
                cleanup_exc: BaseException | None = None
                restore_exc: BaseException | None = None
                if backup_name and entry_exists(root_fd, backup_name):
                    if entry_exists(root_fd, SKILL_NAME):
                        try:
                            remove_entry_at(root_fd, SKILL_NAME)
                        except Exception as err:
                            cleanup_exc = err
                    try:
                        rename_entry(root_fd, backup_name, SKILL_NAME)
                    except Exception as err:
                        restore_exc = err
                extraneous_exc = restore_exc or cleanup_exc
                if extraneous_exc:
                    raise exc from extraneous_exc
                raise
            else:
                if backup_name and entry_exists(root_fd, backup_name):
                    try:
                        remove_entry_at(root_fd, backup_name)
                    except Exception:
                        pass
            finally:
                if entry_exists(root_fd, staging_name):
                    try:
                        remove_entry_at(root_fd, staging_name)
                    except Exception:
                        pass
                if not (entry_exists(root_fd, staging_name) or (backup_name and entry_exists(root_fd, backup_name))):
                    clear_export_state(root_fd)
                    if commit_published:
                        os.fsync(root_fd)

    return destination


def build_release_zip(output_dir: Path, version: str | None = None, release_tag: str | None = None) -> Path:
    ensure_supported_platform()
    normalized_version = normalize_build_version(version)
    if release_tag is not None:
        normalized_tag = release_tag.strip()
        if not normalized_tag:
            raise ValueError("Release tag cannot be blank.")
        ensure_tag_matches_version(normalized_tag, normalized_version)

    source_root = validate_skill_source(skill_source_dir())
    validate_write_path(output_dir, label="Build output path")
    validate_not_within_source(output_dir, source_root, label="Build output path")

    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"Output path exists and is not a directory: {output_dir}")

    artifact = output_dir / f"{SKILL_NAME}-{normalized_version}.zip"
    temp_artifact_name = f".{SKILL_NAME}-{normalized_version}-{uuid4().hex}.zip.tmp"

    with ensured_directory(output_dir, label="Build output path") as output_fd:
        temp_artifact_fd = os.open(
            temp_artifact_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW_FLAG,
            0o600,
            dir_fd=output_fd,
        )
        try:
            with os.fdopen(temp_artifact_fd, "wb") as temp_artifact:
                with ZipFile(temp_artifact, "w", compression=ZIP_DEFLATED) as archive:
                    for snapshot in snapshot_shippable_skill_files(source_root):
                        with opened_snapshotted_file(snapshot) as source_fd:
                            with archive.open(f"{SKILL_NAME}/{snapshot.relative}", "w") as entry:
                                stream_fd_to_writer(source_fd, entry, label=snapshot.relative)
                temp_artifact.flush()
                os.fsync(temp_artifact.fileno())
            replace_entry(output_fd, temp_artifact_name, artifact.name)
            os.fsync(output_fd)
        except BaseException:
            remove_entry_at(output_fd, temp_artifact_name, ignore_errors=True)
            raise

    return artifact
