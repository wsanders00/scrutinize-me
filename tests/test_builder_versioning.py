from pathlib import Path
import sys
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill.builder_versioning import (
    ensure_tag_matches_version,
    normalize_build_version,
    release_version_from_tag,
    validate_semver,
)


class VersioningTests(unittest.TestCase):
    def test_release_version_from_semver_tag(self) -> None:
        self.assertEqual(release_version_from_tag("v1.2.3"), "1.2.3")

    def test_release_version_rejects_invalid_tag_prefix(self) -> None:
        with self.assertRaises(ValueError):
            release_version_from_tag("1.2.3")

    def test_matching_tag_and_version_are_accepted(self) -> None:
        self.assertEqual(ensure_tag_matches_version(f"v{__version__}", __version__), __version__)

    def test_mismatched_tag_and_version_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ensure_tag_matches_version("v9.9.9", __version__)

    def test_validate_semver_accepts_valid_versions(self) -> None:
        valid_versions = [
            "0.1.0",
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-0alpha",
            "1.0.0-0A",
            "1.0.0+build.1",
            "2.10.3",
        ]
        for version in valid_versions:
            with self.subTest(version=version):
                self.assertEqual(validate_semver(version), version)

    def test_validate_semver_rejects_invalid_versions(self) -> None:
        invalid_versions = [
            "1.0",
            "01.0.0",
            "1.0.0-",
            "1.0.0-01",
            "1.0.0+build^1",
        ]
        for version in invalid_versions:
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    validate_semver(version)

    def test_normalize_build_version_defaults_to_package_version(self) -> None:
        self.assertEqual(normalize_build_version(None), __version__)

    def test_normalize_build_version_accepts_matching_package_version(self) -> None:
        self.assertEqual(normalize_build_version(__version__), __version__)

    def test_normalize_build_version_rejects_mismatched_package_version(self) -> None:
        with self.assertRaises(ValueError):
            normalize_build_version("1.2.3")


if __name__ == "__main__":
    unittest.main()
