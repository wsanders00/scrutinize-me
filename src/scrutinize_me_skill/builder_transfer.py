from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat

from scrutinize_me_skill.builder_fs import ensured_directory, opened_parent_directory, write_all
from scrutinize_me_skill.builder_platform import NOFOLLOW_FLAG
from scrutinize_me_skill.manifest import (
    REQUIRED_SKILL_FILES,
    SKILL_NAME,
    is_shippable_relative_path,
)


@dataclass(frozen=True)
class ShippableFileSnapshot:
    path: Path
    relative: str
    device: int
    inode: int
    size: int
    mtime_ns: int


def _matches_snapshot(snapshot: ShippableFileSnapshot, info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_dev == snapshot.device
        and info.st_ino == snapshot.inode
        and info.st_size == snapshot.size
        and info.st_mtime_ns == snapshot.mtime_ns
    )


def skill_source_dir() -> Path:
    return Path(__file__).resolve().parent / "skill" / SKILL_NAME


def validate_skill_source(source_root: Path) -> Path:
    root = source_root.resolve()
    if not root.exists():
        raise FileNotFoundError(f"Skill source directory not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Skill source path is not a directory: {root}")

    missing = [relative for relative in REQUIRED_SKILL_FILES if not (root / relative).is_file()]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Skill source is missing required files: {missing_list}")

    return root


def iter_shippable_skill_files(source_root: Path | None = None) -> list[tuple[Path, str]]:
    root = (source_root or skill_source_dir()).resolve()
    shipped: list[tuple[Path, str]] = []

    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not allowed in the shipped skill payload: {path}")
        if not path.is_file():
            continue

        rel = PurePosixPath(path.relative_to(root).as_posix())
        if not is_shippable_relative_path(rel):
            continue

        shipped.append((path, rel.as_posix()))

    return shipped


def snapshot_shippable_skill_files(
    source_root: Path | None = None,
    *,
    iter_files=iter_shippable_skill_files,
) -> list[ShippableFileSnapshot]:
    snapshots: list[ShippableFileSnapshot] = []
    for path, relative in iter_files(source_root):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Symlinks are not allowed in the shipped skill payload: {path}")
        if info.st_nlink > 1:
            raise ValueError(f"Hard links are not allowed in the shipped skill payload: {path}")
        snapshots.append(
            ShippableFileSnapshot(
                path=path,
                relative=relative,
                device=info.st_dev,
                inode=info.st_ino,
                size=info.st_size,
                mtime_ns=info.st_mtime_ns,
            )
        )
    return snapshots


@contextmanager
def opened_snapshotted_file(
    snapshot: ShippableFileSnapshot,
    *,
    nofollow_flag: int = NOFOLLOW_FLAG,
):
    fd = os.open(snapshot.path, os.O_RDONLY | nofollow_flag)
    try:
        info = os.fstat(fd)
        if not _matches_snapshot(snapshot, info):
            raise ValueError(f"Shipped file changed during export/build: {snapshot.path}")
        yield fd
        info = os.fstat(fd)
        if not _matches_snapshot(snapshot, info):
            raise ValueError(f"Shipped file changed during export/build: {snapshot.path}")
    finally:
        os.close(fd)


def stream_fd_to_fd(source_fd: int, destination_fd: int, *, label: str, write_chunk=write_all) -> None:
    while True:
        chunk = os.read(source_fd, 65536)
        if not chunk:
            break
        write_chunk(destination_fd, chunk, label=label)


def stream_fd_to_writer(source_fd: int, writer, *, label: str) -> None:
    while True:
        chunk = os.read(source_fd, 65536)
        if not chunk:
            break
        remaining = memoryview(chunk)
        while remaining:
            written = writer.write(remaining)
            if written is None:
                raise OSError(f"Writer returned None while writing {label}")
            if written <= 0:
                raise OSError(f"Short write while writing {label}")
            remaining = remaining[written:]


def copy_shippable_skill_tree(
    source_root: Path,
    destination: Path,
    *,
    destination_fd: int | None = None,
    snapshot_files=snapshot_shippable_skill_files,
    open_snapshot=opened_snapshotted_file,
    open_parent=opened_parent_directory,
    ensure_directory=ensured_directory,
    stream_to_fd=stream_fd_to_fd,
    nofollow_flag: int = NOFOLLOW_FLAG,
    sync_fd=None,
) -> None:
    if sync_fd is None:
        sync_fd = os.fsync

    if destination_fd is None:
        with ensure_directory(destination, label="Copy destination") as destination_root_fd:
            copy_shippable_skill_tree(
                source_root,
                destination,
                destination_fd=destination_root_fd,
                snapshot_files=snapshot_files,
                open_snapshot=open_snapshot,
                open_parent=open_parent,
                ensure_directory=ensure_directory,
                stream_to_fd=stream_to_fd,
                nofollow_flag=nofollow_flag,
                sync_fd=sync_fd,
            )
        return

    for snapshot in snapshot_files(source_root):
        with open_snapshot(snapshot) as source_fd:
            relative_path = PurePosixPath(snapshot.relative)
            with open_parent(destination_fd, relative_path.as_posix()) as parent_fd:
                file_fd = os.open(
                    relative_path.name,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | nofollow_flag,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    stream_to_fd(source_fd, file_fd, label=snapshot.relative)
                    sync_fd(file_fd)
                finally:
                    os.close(file_fd)
