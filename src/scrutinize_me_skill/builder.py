from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from pathlib import PurePosixPath
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix-only locking in current support matrix.
    fcntl = None

from scrutinize_me_skill import __version__
from scrutinize_me_skill.manifest import (
    REQUIRED_SKILL_FILES,
    SKILL_NAME,
    is_shippable_relative_path,
)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
RECOVERY_DIR_PATTERNS = {
    "backup": re.compile(rf"^\.{re.escape(SKILL_NAME)}-backup-[0-9a-f]+$"),
    "staging": re.compile(rf"^\.{re.escape(SKILL_NAME)}-staging-[0-9a-f]+$"),
}
EXPORT_LOCK_NAME = f".{SKILL_NAME}-export.lock"
STATE_FILE_VERSION = 1
EXPORT_STATE_MAX_BYTES = 1 << 20
NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAG = getattr(os, "O_DIRECTORY", 0)
INVALID_EXPORT_STATE_PREFIX = f".{SKILL_NAME}-export-state.invalid-"


@dataclass(frozen=True)
class ShippableFileSnapshot:
    path: Path
    relative: str
    device: int
    inode: int
    size: int
    mtime_ns: int


def skill_source_dir() -> Path:
    return Path(__file__).resolve().parent / "skill" / SKILL_NAME


def export_state_path(target_root: Path) -> Path:
    return target_root / f".{SKILL_NAME}-export-state.json"


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


def trusted_symlink_scan_base(path: Path) -> Path | None:
    candidates = [
        Path.cwd(),
        Path(tempfile.gettempdir()),
        Path("/tmp"),
        Path("/private/tmp"),
    ]
    matches: list[Path] = []
    seen: set[str] = set()

    # Absolute temp paths on macOS are commonly rooted under /var/... even
    # though the real path lives under /private/var/.... Trusting the tempdir
    # prefix avoids rejecting those aliases while still checking beneath it.
    for candidate in candidates:
        for base in (candidate, candidate.resolve(strict=False)):
            key = str(base)
            if key in seen:
                continue
            seen.add(key)
            try:
                path.relative_to(base)
            except ValueError:
                continue
            matches.append(base)

    if not matches:
        return None

    return max(matches, key=lambda base: len(base.parts))


def find_symlink_component(path: Path) -> Path | None:
    if path.is_absolute():
        current = trusted_symlink_scan_base(path) or Path(path.anchor)
        parts = path.relative_to(current).parts if current != Path(path.anchor) else path.parts[1:]
    else:
        current = Path()
        parts = path.parts

    for part in parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def validate_write_path(path: Path, *, label: str) -> None:
    if component := find_symlink_component(path):
        raise ValueError(f"{label} contains a symlinked path component: {component}")


def validate_not_within_source(path: Path, source_root: Path, *, label: str) -> None:
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(source_root)
    except ValueError:
        return
    raise ValueError(f"{label} cannot be inside the skill source directory: {path}")


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


def export_state_name() -> str:
    return f".{SKILL_NAME}-export-state.json"


class ExportStateInvalidError(ValueError):
    pass


class ExportStateOversizeError(ExportStateInvalidError):
    pass


def read_export_state_bytes(root_fd: int, state_name: str, state_path: Path) -> bytes:
    try:
        state_fd = os.open(state_name, os.O_RDONLY | NOFOLLOW_FLAG, dir_fd=root_fd)
    except OSError as exc:
        raise ValueError(f"Invalid export state file: {state_path}") from exc
    try:
        info = os.fstat(state_fd)
        if not stat.S_ISREG(info.st_mode):
            raise ExportStateInvalidError(f"Invalid export state file: {state_path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(state_fd, 65536)
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
        os.close(state_fd)


def ensure_supported_platform() -> None:
    missing: list[str] = []
    if os.name != "posix":
        missing.append(f"os.name={os.name!r}")
    if fcntl is None:
        missing.append("fcntl.flock")
    if not hasattr(os, "fwalk"):
        missing.append("os.fwalk")
    if DIRECTORY_FLAG == 0:
        missing.append("os.O_DIRECTORY")
    if NOFOLLOW_FLAG == 0:
        missing.append("os.O_NOFOLLOW")

    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            "scrutinize-me export/build requires a POSIX environment with "
            "fcntl.flock, os.fwalk, os.O_DIRECTORY, and os.O_NOFOLLOW; "
            f"unsupported platform or missing capabilities: {missing_list}"
        )


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
                os.mkdir(part, dir_fd=current_fd)
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


@contextmanager
def opened_parent_directory(root_fd: int, relative: str):
    current_fd = os.dup(root_fd)
    parts = PurePosixPath(relative).parent.parts
    try:
        for part in parts:
            try:
                os.mkdir(part, dir_fd=current_fd)
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


def write_bytes_at(root_fd: int, relative: str, data: bytes) -> None:
    relative_path = PurePosixPath(relative)
    with opened_parent_directory(root_fd, relative_path.as_posix()) as parent_fd:
        file_fd = os.open(
            relative_path.name,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | NOFOLLOW_FLAG,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(file_fd, remaining)
                if written <= 0:
                    raise OSError(f"Short write while writing {relative}")
                remaining = remaining[written:]
        finally:
            os.close(file_fd)


def snapshot_shippable_skill_files(source_root: Path | None = None) -> list[ShippableFileSnapshot]:
    snapshots: list[ShippableFileSnapshot] = []
    for path, relative in iter_shippable_skill_files(source_root):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Symlinks are not allowed in the shipped skill payload: {path}")
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


def read_snapshotted_file(snapshot: ShippableFileSnapshot) -> bytes:
    fd = os.open(snapshot.path, os.O_RDONLY | NOFOLLOW_FLAG)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_dev != snapshot.device
            or info.st_ino != snapshot.inode
            or info.st_size != snapshot.size
            or info.st_mtime_ns != snapshot.mtime_ns
        ):
            raise ValueError(f"Shipped file changed during export/build: {snapshot.path}")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def copy_shippable_skill_tree(
    source_root: Path,
    destination: Path,
    *,
    destination_fd: int | None = None,
) -> None:
    if destination_fd is None:
        with ensured_directory(destination, label="Copy destination") as destination_root_fd:
            copy_shippable_skill_tree(
                source_root,
                destination,
                destination_fd=destination_root_fd,
            )
        return

    for snapshot in snapshot_shippable_skill_files(source_root):
        data = read_snapshotted_file(snapshot)
        write_bytes_at(destination_fd, snapshot.relative, data)


def write_export_state(root_fd: int, target_root: Path, *, staging_name: str, backup_name: str | None) -> None:
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
    temp_state_name = f".{state_name}.{uuid4().hex}.tmp"
    temp_fd = os.open(
        temp_state_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW_FLAG,
        0o600,
        dir_fd=root_fd,
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8", closefd=False) as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        replace_entry(root_fd, temp_state_name, state_name)
        os.fsync(root_fd)
    except BaseException:
        remove_entry_at(root_fd, temp_state_name, ignore_errors=True)
        raise
    finally:
        os.close(temp_fd)


def clear_export_state(root_fd: int) -> None:
    remove_entry_at(root_fd, export_state_name(), ignore_errors=True)


def quarantine_export_state(root_fd: int) -> None:
    invalid_name = f"{INVALID_EXPORT_STATE_PREFIX}{uuid4().hex}.json"
    replace_entry(root_fd, export_state_name(), invalid_name)
    os.fsync(root_fd)


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


def recover_export_state(target_root: Path, root_fd: int, destination_name: str) -> None:
    state_name = export_state_name()
    state_path = export_state_path(target_root)
    if not entry_exists(root_fd, state_name):
        return

    try:
        raw_state = read_export_state_bytes(root_fd, state_name, state_path)
    except ExportStateInvalidError:
        quarantine_export_state(root_fd)
        return

    try:
        state = json.loads(raw_state.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        quarantine_export_state(root_fd)
        return
    if not isinstance(state, dict):
        quarantine_export_state(root_fd)
        return

    state_version = state.get("state_version", STATE_FILE_VERSION)
    if state_version != STATE_FILE_VERSION:
        if isinstance(state_version, int) and state_version > STATE_FILE_VERSION:
            quarantine_export_state(root_fd)
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

    if not ((backup_name and entry_exists(root_fd, backup_name)) or (staging_name and entry_exists(root_fd, staging_name))):
        clear_export_state(root_fd)


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
            os.mkdir(staging_name, dir_fd=root_fd)
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
            except BaseException:
                remove_entry_at(root_fd, staging_name, ignore_errors=True)
                raise

            try:
                write_export_state(
                    root_fd,
                    target_root,
                    staging_name=staging_name,
                    backup_name=backup_name,
                )
                if backup_name is not None:
                    rename_entry(root_fd, SKILL_NAME, backup_name)
                rename_entry(root_fd, staging_name, SKILL_NAME)
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
                        archive.writestr(
                            f"{SKILL_NAME}/{snapshot.relative}",
                            read_snapshotted_file(snapshot),
                        )
            replace_entry(output_fd, temp_artifact_name, artifact.name)
        except BaseException:
            remove_entry_at(output_fd, temp_artifact_name, ignore_errors=True)
            raise

    return artifact
