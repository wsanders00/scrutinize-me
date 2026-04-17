from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill.builder import (
    build_release_zip,
    ensure_tag_matches_version,
    materialize_skill,
    release_version_from_tag,
)


class VersioningTests(unittest.TestCase):
    def test_release_version_from_semver_tag(self) -> None:
        self.assertEqual(release_version_from_tag("v1.2.3"), "1.2.3")

    def test_release_version_rejects_invalid_tag_prefix(self) -> None:
        with self.assertRaises(ValueError):
            release_version_from_tag("1.2.3")

    def test_matching_tag_and_version_are_accepted(self) -> None:
        self.assertEqual(ensure_tag_matches_version("v0.1.0", __version__), __version__)

    def test_mismatched_tag_and_version_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ensure_tag_matches_version("v9.9.9", __version__)


class ReleaseBundleTests(unittest.TestCase):
    def test_materialize_skill_copies_agent_skill_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_root = Path(tmp_dir) / ".agents" / "skills"
            skill_dir = materialize_skill(export_root)

            self.assertEqual(skill_dir.name, "scrutinize-me")
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertTrue((skill_dir / "agents" / "openai.yaml").exists())
            self.assertTrue((skill_dir / "evals" / "evals.json").exists())
            self.assertTrue((skill_dir / "references" / "review-template.md").exists())
            self.assertTrue((skill_dir / "references" / "reviewer-personas.md").exists())
            self.assertTrue((skill_dir / "references" / "orchestrator-playbook.md").exists())
            self.assertTrue((skill_dir / "references" / "output-schema.md").exists())

    def test_build_release_zip_contains_skill_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact = build_release_zip(output_dir=Path(tmp_dir), version=__version__)

            self.assertEqual(artifact.name, f"scrutinize-me-{__version__}.zip")
            self.assertTrue(artifact.exists())

            with zipfile.ZipFile(artifact) as archive:
                archive_names = set(archive.namelist())

            self.assertIn("scrutinize-me/SKILL.md", archive_names)
            self.assertIn("scrutinize-me/agents/openai.yaml", archive_names)
            self.assertIn("scrutinize-me/evals/evals.json", archive_names)
            self.assertIn("scrutinize-me/references/reviewer-personas.md", archive_names)
            self.assertIn("scrutinize-me/references/orchestrator-playbook.md", archive_names)
            self.assertIn("scrutinize-me/references/output-schema.md", archive_names)
            self.assertIn("scrutinize-me/references/review-template.md", archive_names)


if __name__ == "__main__":
    unittest.main()
