import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
import zipfile


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill.builder import (
    SKILL_NAME,
    build_release_zip,
    ensure_tag_matches_version,
    iter_shippable_skill_files,
    materialize_skill,
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
        self.assertEqual(ensure_tag_matches_version("v0.1.0", __version__), __version__)

    def test_mismatched_tag_and_version_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ensure_tag_matches_version("v9.9.9", __version__)

    def test_validate_semver_accepts_valid_versions(self) -> None:
        valid_versions = [
            "0.1.0",
            "1.0.0-alpha",
            "1.0.0-alpha.1",
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
            "1.0.0+build^1",
        ]
        for version in invalid_versions:
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    validate_semver(version)


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

    def test_materialize_skill_preserves_existing_export_on_copy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / "scrutinize-me"
            destination.mkdir(parents=True)
            existing_marker = destination / "existing.txt"
            existing_marker.write_text("keep", encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.copy_shippable_skill_tree",
                    side_effect=RuntimeError("boom"),
                ):
                    with self.assertRaises(RuntimeError):
                        materialize_skill(export_root, force=True)

            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())

    def test_materialize_skill_restores_after_staging_rename_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / "scrutinize-me"
            destination.mkdir(parents=True)
            existing_marker = destination / "existing.txt"
            existing_marker.write_text("keep", encoding="utf-8")

            stage_uuid = uuid.UUID("00000000000000000000000000000001")
            backup_uuid = uuid.UUID("00000000000000000000000000000002")
            uuid_sequence = [stage_uuid, backup_uuid]
            staging_path = export_root / f".{SKILL_NAME}-staging-{stage_uuid.hex}"

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_rename(source: Path, target: Path) -> None:
                if source == staging_path:
                    raise RuntimeError("staging rename failed")
                source.rename(target)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.rename_path", side_effect=fake_rename
                    ):
                        with self.assertRaises(RuntimeError) as context:
                            materialize_skill(export_root, force=True)

            self.assertEqual(str(context.exception), "staging rename failed")
            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())
            self.assertFalse(staging_path.exists())

    def test_materialize_skill_ignores_backup_cleanup_failure_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / "scrutinize-me"
            destination.mkdir(parents=True)
            stale_marker = destination / "stale.txt"
            stale_marker.write_text("stale", encoding="utf-8")

            stage_uuid = uuid.UUID("00000000000000000000000000000001")
            backup_uuid = uuid.UUID("00000000000000000000000000000002")
            uuid_sequence = [stage_uuid, backup_uuid]
            backup_path = export_root / f".{SKILL_NAME}-backup-{backup_uuid.hex}"
            from scrutinize_me_skill import builder as builder_module
            real_remove_tree = builder_module.remove_tree

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_remove_tree(path: Path, *, ignore_errors: bool = False) -> None:
                if Path(path) == backup_path:
                    raise OSError("backup cleanup failed")
                real_remove_tree(path, ignore_errors=ignore_errors)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.remove_tree", side_effect=fake_remove_tree
                    ):
                        skill_dir = materialize_skill(export_root, force=True)

            self.assertEqual(skill_dir, destination)
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(stale_marker.exists())
            self.assertTrue(backup_path.exists())

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

    def test_build_release_zip_rejects_file_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "dist"
            output_path.write_text("not a dir", encoding="utf-8")

            with self.assertRaises(ValueError) as context:
                build_release_zip(output_dir=output_path, version=__version__)

        self.assertIn("not a directory", str(context.exception))


class ShippableFilesTests(unittest.TestCase):
    def _create_skill_layout(self, root: Path) -> Path:
        skill_root = root / "scrutinize-me"
        (skill_root / "agents").mkdir(parents=True)
        (skill_root / "evals").mkdir(parents=True)
        (skill_root / "references").mkdir(parents=True)
        (skill_root / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        (skill_root / "agents" / "openai.yaml").write_text("model: gpt\n", encoding="utf-8")
        config_dir = skill_root / "agents" / "config"
        config_dir.mkdir(parents=True)
        (config_dir / "override.json").write_text("{}\n", encoding="utf-8")
        (skill_root / "evals" / "evals.json").write_text("{}\n", encoding="utf-8")
        (skill_root / "references" / "reviewer-personas.md").write_text("# Personas\n", encoding="utf-8")
        guide_dir = skill_root / "references" / "guide"
        guide_dir.mkdir(parents=True)
        (guide_dir / "deck.md").write_text("guide\n", encoding="utf-8")
        (skill_root / "references" / "orchestrator-playbook.md").write_text("# Playbook\n", encoding="utf-8")
        (skill_root / "references" / "review-template.md").write_text("# Template\n", encoding="utf-8")
        (skill_root / "references" / "output-schema.md").write_text("schema\n", encoding="utf-8")
        (skill_root / "references" / ".hidden").write_text("secret\n", encoding="utf-8")
        (skill_root / "notes.txt").write_text("ignore\n", encoding="utf-8")
        return skill_root

    def test_iter_shippable_skill_files_returns_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = self._create_skill_layout(Path(tmp_dir))
            shipped = iter_shippable_skill_files(skill_root)

        relative_names = {rel for _, rel in shipped}
        expected = {
            "SKILL.md",
            "agents/openai.yaml",
            "agents/config/override.json",
            "evals/evals.json",
            "references/reviewer-personas.md",
            "references/orchestrator-playbook.md",
            "references/review-template.md",
            "references/output-schema.md",
            "references/guide/deck.md",
        }

        self.assertEqual(relative_names, expected)


if __name__ == "__main__":
    unittest.main()
