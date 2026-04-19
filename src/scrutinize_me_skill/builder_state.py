from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from uuid import uuid4

from scrutinize_me_skill.builder_fs import (
    entry_exists,
    lstat_entry,
    remove_entry_at,
    rename_entry,
    replace_entry,
)
from scrutinize_me_skill.builder_platform import NOFOLLOW_FLAG
from scrutinize_me_skill.manifest import SKILL_NAME


RECOVERY_DIR_PATTERNS = {
    "backup": re.compile(rf"^\.{re.escape(SKILL_NAME)}-backup-[0-9a-f]+$"),
    "staging": re.compile(rf"^\.{re.escape(SKILL_NAME)}-staging-[0-9a-f]+$"),
}
STATE_FILE_VERSION = 1
EXPORT_STATE_MAX_BYTES = 1 << 20
INVALID_EXPORT_STATE_PREFIX = f".{SKILL_NAME}-export-state.invalid-"


def export_state_path(target_root: Path) -> Path:
    return target_root / f".{SKILL_NAME}-export-state.json"


def export_state_name() -> str:
    return f".{SKILL_NAME}-export-state.json"


class ExportStateInvalidError(ValueError):
    pass


class ExportStateOversizeError(ExportStateInvalidError):
    pass


def read_export_state_bytes(
    root_fd: int,
    state_name: str,
    state_path: Path,
    *,
    nofollow_flag: int = NOFOLLOW_FLAG,
    os_module=os,
) -> bytes:
    try:
        state_fd = os_module.open(state_name, os.O_RDONLY | nofollow_flag, dir_fd=root_fd)
    except OSError as exc:
        raise ValueError(f"Invalid export state file: {state_path}") from exc
    try:
        info = os_module.fstat(state_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ExportStateInvalidError(f"Invalid export state file: {state_path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os_module.read(state_fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > EXPORT_STATE_MAX_BYTES:
                raise ExportStateOversizeError(
                    f"Export state exceeds {EXPORT_STATE_MAX_BYTES} bytes: {state_path}"
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os_module.close(state_fd)


def write_export_state(
    root_fd: int,
    target_root: Path,
    *,
    staging_name: str,
    backup_name: str | None,
    replace=replace_entry,
    remove=remove_entry_at,
    id_factory=uuid4,
    nofollow_flag: int = NOFOLLOW_FLAG,
    os_module=os,
) -> None:
    state_name = export_state_name()
    state_path = export_state_path(target_root)
    existing = lstat_entry(root_fd, state_name)
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise ValueError(f"Invalid export state file: {state_path}")

    state = {
        "state_version": STATE_FILE_VERSION,
        "staging_dir": staging_name,
        "backup_dir": backup_name,
    }
    temp_state_name = f".{state_name}.{id_factory().hex}.tmp"
    temp_fd = os_module.open(
        temp_state_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag,
        0o600,
        dir_fd=root_fd,
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(state, handle)
            handle.flush()
            os_module.fsync(handle.fileno())
        replace(root_fd, temp_state_name, state_name)
        os_module.fsync(root_fd)
    except BaseException:
        remove(root_fd, temp_state_name, ignore_errors=True)
        raise
    finally:
        os_module.close(temp_fd)


def clear_export_state(root_fd: int, *, remove=remove_entry_at) -> None:
    remove(root_fd, export_state_name(), ignore_errors=True)


def quarantine_export_state(
    root_fd: int,
    *,
    replace=replace_entry,
    id_factory=uuid4,
    os_module=os,
) -> None:
    invalid_name = f"{INVALID_EXPORT_STATE_PREFIX}{id_factory().hex}.json"
    replace(root_fd, export_state_name(), invalid_name)
    os_module.fsync(root_fd)


def validated_export_state_name(
    root_fd: int,
    target_root: Path,
    entry_name: object,
    *,
    kind: str,
    state_path: Path,
) -> str | None:
    if entry_name is None:
        return None

    pattern = RECOVERY_DIR_PATTERNS[kind]
    if not isinstance(entry_name, str) or not pattern.fullmatch(entry_name):
        raise ValueError(f"Invalid export state {kind}_dir entry in {state_path}: {entry_name!r}")

    info = lstat_entry(root_fd, entry_name)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"Invalid export state {kind}_dir entry in {state_path}: {entry_name!r}")
    return entry_name


def recover_export_state(
    target_root: Path,
    root_fd: int,
    destination_name: str,
    *,
    read_bytes=read_export_state_bytes,
    quarantine=quarantine_export_state,
    clear=clear_export_state,
) -> None:
    state_name = export_state_name()
    state_path = export_state_path(target_root)
    if not entry_exists(root_fd, state_name):
        return

    try:
        raw_state = read_bytes(root_fd, state_name, state_path)
    except ExportStateInvalidError:
        quarantine(root_fd)
        return

    try:
        state = json.loads(raw_state.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        quarantine(root_fd)
        return
    if not isinstance(state, dict):
        quarantine(root_fd)
        return

    state_version = state.get("state_version", STATE_FILE_VERSION)
    if state_version != STATE_FILE_VERSION:
        if isinstance(state_version, int) and state_version > STATE_FILE_VERSION:
            quarantine(root_fd)
            return
        raise ValueError(f"Incomplete export state detected: {state_path}")

    staging_name = validated_export_state_name(
        root_fd,
        target_root,
        state.get("staging_dir"),
        kind="staging",
        state_path=state_path,
    )
    backup_name = validated_export_state_name(
        root_fd,
        target_root,
        state.get("backup_dir"),
        kind="backup",
        state_path=state_path,
    )

    destination_info = lstat_entry(root_fd, destination_name)
    if backup_name and entry_exists(root_fd, backup_name) and destination_info is None:
        rename_entry(root_fd, backup_name, destination_name)
    elif backup_name and entry_exists(root_fd, backup_name) and destination_info is not None:
        remove_entry_at(root_fd, backup_name, ignore_errors=True)

    if staging_name and entry_exists(root_fd, staging_name):
        remove_entry_at(root_fd, staging_name, ignore_errors=True)

    if not (
        (backup_name and entry_exists(root_fd, backup_name))
        or (staging_name and entry_exists(root_fd, staging_name))
    ):
        clear(root_fd)
