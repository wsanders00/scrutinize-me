from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import builder_platform
from scrutinize_me_skill.builder_platform import (
    _directory_identity,
    _filesystem_identity_overlaps,
    ensure_supported_platform,
    validate_no_path_overlap,
)


class PlatformSupportTests(unittest.TestCase):
    def test_ensure_supported_platform_accepts_required_capabilities(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        ensure_supported_platform(
            os_module=fake_os,
            fcntl_module=object(),
            directory_flag=1,
            nofollow_flag=1,
        )

    def test_ensure_supported_platform_rejects_non_posix(self) -> None:
        fake_os = types.SimpleNamespace(name="nt", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("os.name='nt'", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_fcntl(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=None,
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("fcntl.flock", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_fwalk(self) -> None:
        fake_os = types.SimpleNamespace(name="posix")
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("os.fwalk", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_directory_flag(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=0,
                nofollow_flag=1,
            )

        self.assertIn("os.O_DIRECTORY", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_nofollow_flag(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=0,
            )

        self.assertIn("os.O_NOFOLLOW", str(context.exception))


class PathOverlapTests(unittest.TestCase):
    def test_directory_identity_ignores_missing_and_non_directory_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            directory = root / "directory"
            directory.mkdir()
            file_path = root / "file"
            file_path.write_text("file", encoding="utf-8")

            self.assertIsNotNone(_directory_identity(directory))
            self.assertIsNone(_directory_identity(file_path))
            self.assertIsNone(_directory_identity(root / "missing"))

    def test_directory_identity_does_not_swallow_unexpected_filesystem_errors(self) -> None:
        with mock.patch.object(builder_platform.Path, "stat", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                _directory_identity(Path("/unavailable"))

    def test_identity_overlap_checks_exact_path_against_ancestors_both_directions(self) -> None:
        first = Path("/virtual/export")
        second = Path("/virtual/CaseSource/child")
        identities = {
            first: (1, 10),
            second: (1, 20),
            Path("/virtual/CaseSource"): (1, 10),
            Path("/virtual"): (1, 30),
            Path("/"): (1, 40),
        }

        with mock.patch.object(
            builder_platform,
            "_directory_identity",
            side_effect=lambda path: identities.get(path),
        ):
            self.assertTrue(_filesystem_identity_overlaps(first, second))

        first = Path("/virtual/CaseSource/child")
        second = Path("/virtual/export")
        with mock.patch.object(
            builder_platform,
            "_directory_identity",
            side_effect=lambda path: identities.get(path),
        ):
            self.assertTrue(_filesystem_identity_overlaps(first, second))

    def test_identity_overlap_allows_distinct_case_sensitive_directories(self) -> None:
        first = Path("/virtual/casetarget")
        second = Path("/virtual/CaseTarget/skill")
        identities = {
            first: (1, 10),
            second: (1, 20),
            Path("/virtual/CaseTarget"): (1, 21),
            Path("/virtual"): (1, 30),
            Path("/"): (1, 40),
        }

        with mock.patch.object(
            builder_platform,
            "_directory_identity",
            side_effect=lambda path: identities.get(path),
        ):
            self.assertFalse(_filesystem_identity_overlaps(first, second))

    def test_validate_no_path_overlap_rejects_identity_overlap_before_mutation(self) -> None:
        first = Path("/virtual/casetarget/skill")
        second = Path("/virtual/CaseTarget/skill/nested/source")
        identities = {
            first: (1, 10),
            second: (1, 20),
            Path("/virtual/CaseTarget/skill/nested"): (1, 21),
            Path("/virtual/CaseTarget/skill"): (1, 10),
            Path("/virtual"): (1, 30),
            Path("/"): (1, 40),
        }

        with mock.patch.object(
            builder_platform,
            "_directory_identity",
            side_effect=lambda path: identities.get(path),
        ):
            with self.assertRaises(ValueError):
                validate_no_path_overlap(first, second, label="Export destination")


if __name__ == "__main__":
    unittest.main()
