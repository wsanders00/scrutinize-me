import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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
    def _make_skill_root(self, root: Path) -> Path:
        skill_root = root / "scrutinize-me"
        (skill_root / "agents").mkdir(parents=True, exist_ok=True)
        (skill_root / "evals").mkdir(parents=True, exist_ok=True)
        (skill_root / "references").mkdir(parents=True, exist_ok=True)
        (skill_root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (skill_root / "agents" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
        (skill_root / "evals" / "evals.json").write_text("{}", encoding="utf-8")
        (skill_root / "references" / "reviewer-personas.md").write_text(
            "# Personas\n", encoding="utf-8"
        )
        return skill_root

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

    def test_materialize_skill_rejects_existing_destination_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / "scrutinize-me"
            destination.mkdir(parents=True)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(FileExistsError) as context:
                    materialize_skill(export_root)

            self.assertIn("rerun with --force", str(context.exception))

    def test_materialize_skill_overwrites_existing_destination_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / "scrutinize-me"
            destination.mkdir(parents=True)
            stale_marker = destination / "stale.txt"
            stale_marker.write_text("stale", encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root, force=True)

            self.assertEqual(skill_dir, destination)
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(stale_marker.exists())

    def test_materialize_skill_rejects_self_target_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(skill_root)

            self.assertIn("source directory", str(context.exception))

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(root)

            self.assertIn("source directory", str(context.exception))

    def test_materialize_skill_rejects_existing_destination_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / "scrutinize-me"
            destination.write_text("not a directory", encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("not a directory", str(context.exception))

    def test_materialize_skill_rejects_existing_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / "scrutinize-me"
            link_target = root / "link-target"
            link_target.mkdir()
            destination.symlink_to(link_target, target_is_directory=True)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))

    def test_materialize_skill_preserves_relative_destination_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = Path("exports")
            cwd = Path.cwd()

            try:
                os.chdir(root)
                with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                    skill_dir = materialize_skill(export_root)
            finally:
                os.chdir(cwd)

            self.assertEqual(skill_dir, export_root / "scrutinize-me")
            self.assertFalse(skill_dir.is_absolute())
            self.assertTrue((root / skill_dir / "SKILL.md").exists())

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

    def test_build_release_zip_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root)
            symlink_path = skill_root / "references" / "alias.md"
            symlink_path.symlink_to(skill_root / "SKILL.md")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=root / "dist", version=__version__)

    def test_build_release_zip_excludes_junk_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root)
            (skill_root / "notes.txt").write_text("junk", encoding="utf-8")
            (skill_root / "references" / ".hidden.md").write_text("hidden", encoding="utf-8")
            (skill_root / "references" / "__pycache__").mkdir()
            (skill_root / "references" / "__pycache__" / "cached.py").write_text(
                "print('cache')\n", encoding="utf-8"
            )
            (skill_root / "references" / "compiled.pyc").write_bytes(b"\x00\x00")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                artifact = build_release_zip(output_dir=root / "dist", version=__version__)

            with zipfile.ZipFile(artifact) as archive:
                archive_names = set(archive.namelist())

            self.assertIn("scrutinize-me/SKILL.md", archive_names)
            self.assertNotIn("scrutinize-me/notes.txt", archive_names)
            self.assertNotIn("scrutinize-me/references/.hidden.md", archive_names)
            self.assertNotIn("scrutinize-me/references/__pycache__/cached.py", archive_names)
            self.assertNotIn("scrutinize-me/references/compiled.pyc", archive_names)


if __name__ == "__main__":
    unittest.main()
