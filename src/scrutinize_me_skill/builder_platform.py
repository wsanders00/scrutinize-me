from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Unix-only locking in current support matrix.
    fcntl = None


NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAG = getattr(os, "O_DIRECTORY", 0)


def ensure_supported_platform(
    *,
    os_module=os,
    fcntl_module=fcntl,
    directory_flag: int = DIRECTORY_FLAG,
    nofollow_flag: int = NOFOLLOW_FLAG,
) -> None:
    missing: list[str] = []
    if os_module.name != "posix":
        missing.append(f"os.name={os_module.name!r}")
    if fcntl_module is None:
        missing.append("fcntl.flock")
    if not hasattr(os_module, "fwalk"):
        missing.append("os.fwalk")
    if directory_flag == 0:
        missing.append("os.O_DIRECTORY")
    if nofollow_flag == 0:
        missing.append("os.O_NOFOLLOW")

    if missing:
        missing_list = ", ".join(missing)
        raise RuntimeError(
            "scrutinize-me export/build requires a POSIX environment with "
            "fcntl.flock, os.fwalk, os.O_DIRECTORY, and os.O_NOFOLLOW; "
            f"unsupported platform or missing capabilities: {missing_list}"
        )


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


def _directory_identity(path: Path) -> tuple[int, int] | None:
    """Return the filesystem identity for an existing directory.

    Missing paths are expected while validating a new export destination, so
    they are unavailable for identity comparison.  Other filesystem errors
    remain visible to the caller instead of being mistaken for a safe path.
    """

    try:
        info = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None

    if not stat.S_ISDIR(info.st_mode):
        return None
    return info.st_dev, info.st_ino


def _existing_directory_identities(path: Path) -> set[tuple[int, int]]:
    """Collect identities for a path and its existing directory ancestors."""

    identities: set[tuple[int, int]] = set()
    current = path
    while True:
        if identity := _directory_identity(current):
            identities.add(identity)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return identities


def _filesystem_identity_overlaps(first: Path, second: Path) -> bool:
    """Check exact/ancestor overlap using existing filesystem identities.

    Only an exact path on one side is compared with the other side's
    ancestors.  Comparing both ancestor sets would make every pair of paths
    under a shared root appear to overlap.
    """

    first_identity = _directory_identity(first)
    if first_identity is not None and first_identity in _existing_directory_identities(second):
        return True

    second_identity = _directory_identity(second)
    if second_identity is not None and second_identity in _existing_directory_identities(first):
        return True

    return False


def validate_no_path_overlap(first: Path, second: Path, *, label: str) -> None:
    resolved_first = first.resolve(strict=False)
    resolved_second = second.resolve(strict=False)
    try:
        resolved_first.relative_to(resolved_second)
    except ValueError:
        try:
            resolved_second.relative_to(resolved_first)
        except ValueError:
            if not _filesystem_identity_overlaps(resolved_first, resolved_second):
                return
    raise ValueError(f"{label} overlaps the skill source directory: {first}")
