from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat

from scrutinize_me_skill.builder_platform import (
    DIRECTORY_FLAG,
    NOFOLLOW_FLAG,
    fcntl,
    trusted_symlink_scan_base,
)
from scrutinize_me_skill.manifest import SKILL_NAME


EXPORT_LOCK_NAME = f".{SKILL_NAME}-export.lock"


def lstat_entry(root_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.lstat(name, dir_fd=root_fd)
    except FileNotFoundError:
        return None


def entry_exists(root_fd: int, name: str) -> bool:
    return lstat_entry(root_fd, name) is not None


def raise_if_symlink_directory_error(
    exc: OSError,
    *,
    label: str,
    display_path: Path | str,
    root_fd: int | None = None,
    entry_name: str | None = None,
) -> None:
    symlink_errnos = {
        getattr(os, "ELOOP", 40),
        getattr(os, "ENOTDIR", 20),
    }
    if exc.errno not in symlink_errnos:
        raise exc

    try:
        if root_fd is None:
            info = os.lstat(display_path)
        else:
            lookup_name = entry_name if entry_name is not None else os.fspath(display_path)
            info = os.lstat(lookup_name, dir_fd=root_fd)
    except FileNotFoundError:
        raise exc

    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"{label} contains a symlinked path component: {display_path}") from exc

    raise exc


@contextmanager
def opened_directory(path: Path, *, label: str):
    try:
        fd = os.open(path, os.O_RDONLY | DIRECTORY_FLAG | NOFOLLOW_FLAG)
    except OSError as exc:
        raise_if_symlink_directory_error(exc, label=label, display_path=path)
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def opened_directory_at(root_fd: int, name: str, *, label: str):
    try:
        fd = os.open(name, os.O_RDONLY | DIRECTORY_FLAG | NOFOLLOW_FLAG, dir_fd=root_fd)
    except OSError as exc:
        raise_if_symlink_directory_error(
            exc,
            label=label,
            display_path=name,
            root_fd=root_fd,
            entry_name=name,
        )
    try:
        yield fd
    finally:
        os.close(fd)


@contextmanager
def ensured_directory(path: Path, *, label: str):
    if path.is_absolute():
        trusted_base = trusted_symlink_scan_base(path)
        if trusted_base is None:
            current_fd = os.open(path.anchor, os.O_RDONLY | DIRECTORY_FLAG)
            parts = path.parts[1:]
        else:
            current_fd = os.open(trusted_base.resolve(strict=False), os.O_RDONLY | DIRECTORY_FLAG)
            parts = path.relative_to(trusted_base).parts
    else:
        current_fd = os.open(Path.cwd(), os.O_RDONLY | DIRECTORY_FLAG)
        parts = path.parts

    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | DIRECTORY_FLAG | NOFOLLOW_FLAG,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                raise_if_symlink_directory_error(
                    exc,
                    label=label,
                    display_path=path,
                    root_fd=current_fd,
                    entry_name=part,
                )
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


@contextmanager
def held_export_lock(root_fd: int):
    if fcntl is None:
        yield
        return

    lock_fd = os.open(
        EXPORT_LOCK_NAME,
        os.O_RDWR | os.O_CREAT | NOFOLLOW_FLAG,
        0o600,
        dir_fd=root_fd,
    )
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FileExistsError("An export is already in progress for this target root.") from exc
        yield
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def remove_tree_at(root_fd: int, name: str, *, ignore_errors: bool = False) -> None:
    if lstat_entry(root_fd, name) is None:
        return

    try:
        for _, dirnames, filenames, dir_fd in os.fwalk(
            name,
            topdown=False,
            dir_fd=root_fd,
            follow_symlinks=False,
        ):
            for filename in filenames:
                os.unlink(filename, dir_fd=dir_fd)
            for dirname in dirnames:
                os.rmdir(dirname, dir_fd=dir_fd)
        os.rmdir(name, dir_fd=root_fd)
    except FileNotFoundError:
        return
    except Exception:
        if not ignore_errors:
            raise


def remove_entry_at(root_fd: int, name: str, *, ignore_errors: bool = False) -> None:
    info = lstat_entry(root_fd, name)
    if info is None:
        return

    try:
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            remove_tree_at(root_fd, name, ignore_errors=ignore_errors)
        else:
            os.unlink(name, dir_fd=root_fd)
    except FileNotFoundError:
        return
    except Exception:
        if not ignore_errors:
            raise


def rename_entry(root_fd: int, source_name: str, target_name: str) -> None:
    os.rename(source_name, target_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)


def replace_entry(root_fd: int, source_name: str, target_name: str) -> None:
    os.replace(source_name, target_name, src_dir_fd=root_fd, dst_dir_fd=root_fd)


def sync_tree_at(root_fd: int, name: str) -> None:
    for _, _, _, dir_fd in os.fwalk(
        name,
        topdown=False,
        dir_fd=root_fd,
        follow_symlinks=False,
    ):
        os.fsync(dir_fd)


@contextmanager
def opened_parent_directory(root_fd: int, relative: str):
    current_fd = os.dup(root_fd)
    parts = PurePosixPath(relative).parent.parts
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | DIRECTORY_FLAG | NOFOLLOW_FLAG,
                    dir_fd=current_fd,
                )
            except OSError as exc:
                raise_if_symlink_directory_error(
                    exc,
                    label="Shipped file destination",
                    display_path=relative,
                    root_fd=current_fd,
                    entry_name=part,
                )
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd
    finally:
        os.close(current_fd)


def write_all(fd: int, data: bytes, *, label: str) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError(f"Short write while writing {label}")
        remaining = remaining[written:]
