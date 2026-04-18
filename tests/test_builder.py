import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock
import zipfile


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill.manifest import REQUIRED_SKILL_FILES, SKILL_NAME
from scrutinize_me_skill.builder import (
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
        (skill_root / "references" / "orchestrator-playbook.md").write_text(
            "# Playbook\n", encoding="utf-8"
        )
        (skill_root / "references" / "review-template.md").write_text(
            "# Template\n", encoding="utf-8"
        )
        (skill_root / "references" / "output-schema.md").write_text(
            "# Schema\n", encoding="utf-8"
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

    def test_materialize_skill_creates_owner_only_files_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_root = Path(tmp_dir) / ".agents" / "skills"
            original_umask = os.umask(0)
            try:
                skill_dir = materialize_skill(export_root)
            finally:
                os.umask(original_umask)

            mode = (skill_dir / "SKILL.md").stat().st_mode & 0o777
            self.assertEqual(mode & 0o022, 0)
            self.assertEqual(mode & 0o600, 0o600)

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

    def test_materialize_skill_rejects_missing_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            missing_root = root / "source" / "scrutinize-me"
            export_root = root / "exports"

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=missing_root):
                with self.assertRaises(FileNotFoundError):
                    materialize_skill(export_root)

            self.assertFalse((export_root / "scrutinize-me").exists())

    def test_materialize_skill_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            for relative in REQUIRED_SKILL_FILES:
                with self.subTest(relative=relative):
                    skill_root = self._make_skill_root(root / relative.replace("/", "_"))
                    export_root = root / "exports" / relative.replace("/", "_")
                    (skill_root / relative).unlink()

                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root
                    ):
                        with self.assertRaises(ValueError) as context:
                            materialize_skill(export_root)

                    self.assertIn(relative, str(context.exception))

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
            state_uuid = uuid.UUID("00000000000000000000000000000003")
            uuid_sequence = [stage_uuid, backup_uuid, state_uuid]
            staging_name = f".{SKILL_NAME}-staging-{stage_uuid.hex}"
            staging_path = export_root / staging_name

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_rename(_: int, source_name: str, target_name: str) -> None:
                if source_name == staging_name and target_name == SKILL_NAME:
                    raise RuntimeError("staging rename failed")
                (export_root / source_name).rename(export_root / target_name)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.rename_entry", side_effect=fake_rename
                    ):
                        with self.assertRaises(RuntimeError) as context:
                            materialize_skill(export_root, force=True)

            self.assertEqual(str(context.exception), "staging rename failed")
            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())
            self.assertFalse(staging_path.exists())

    def test_materialize_skill_chains_restore_failure_after_primary_rename_failure(self) -> None:
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
            state_uuid = uuid.UUID("00000000000000000000000000000003")
            uuid_sequence = [stage_uuid, backup_uuid, state_uuid]
            staging_name = f".{SKILL_NAME}-staging-{stage_uuid.hex}"
            backup_name = f".{SKILL_NAME}-backup-{backup_uuid.hex}"
            backup_path = export_root / backup_name

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_rename(_: int, source_name: str, target_name: str) -> None:
                if source_name == staging_name and target_name == SKILL_NAME:
                    raise RuntimeError("staging rename failed")
                if source_name == backup_name and target_name == SKILL_NAME:
                    raise OSError("backup restore failed")
                (export_root / source_name).rename(export_root / target_name)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.rename_entry", side_effect=fake_rename
                    ):
                        with self.assertRaises(RuntimeError) as context:
                            materialize_skill(export_root, force=True)

            self.assertEqual(str(context.exception), "staging rename failed")
            self.assertIsNotNone(context.exception.__cause__)
            self.assertEqual(str(context.exception.__cause__), "backup restore failed")
            self.assertFalse(destination.exists())
            self.assertTrue(backup_path.exists())

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
            state_uuid = uuid.UUID("00000000000000000000000000000003")
            uuid_sequence = [stage_uuid, backup_uuid, state_uuid]
            backup_name = f".{SKILL_NAME}-backup-{backup_uuid.hex}"
            backup_path = export_root / backup_name
            from scrutinize_me_skill import builder as builder_module
            real_remove_entry_at = builder_module.remove_entry_at

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_remove_entry_at(root_fd: int, name: str, *, ignore_errors: bool = False) -> None:
                if name == backup_name:
                    raise OSError("backup cleanup failed")
                real_remove_entry_at(root_fd, name, ignore_errors=ignore_errors)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.remove_entry_at", side_effect=fake_remove_entry_at
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

    def test_materialize_skill_rejects_broken_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / "scrutinize-me"
            destination.symlink_to(root / "missing-target", target_is_directory=True)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))

    def test_materialize_skill_rejects_symlinked_target_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            outside = root / "outside"
            outside.mkdir()
            export_root = root / "exports-link"
            export_root.symlink_to(outside, target_is_directory=True)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / "scrutinize-me").exists())

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

    def test_build_release_zip_creates_owner_only_artifact_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)
            original_umask = os.umask(0)
            try:
                artifact = build_release_zip(output_dir=output_dir, version=__version__)
            finally:
                os.umask(original_umask)

            mode = artifact.stat().st_mode & 0o777
            self.assertEqual(mode & 0o022, 0)
            self.assertEqual(mode & 0o600, 0o600)

    def test_build_release_zip_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            for relative in REQUIRED_SKILL_FILES:
                with self.subTest(relative=relative):
                    skill_root = self._make_skill_root(root / relative.replace("/", "_"))
                    output_dir = root / "dist" / relative.replace("/", "_")
                    (skill_root / relative).unlink()

                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root
                    ):
                        with self.assertRaises(ValueError) as context:
                            build_release_zip(output_dir=output_dir, version=__version__)

                    self.assertIn(relative, str(context.exception))

    def test_build_release_zip_replaces_symlink_artifact_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            output_dir.mkdir()
            target = root / "victim.txt"
            target.write_text("victim", encoding="utf-8")
            artifact = output_dir / f"{SKILL_NAME}-{__version__}.zip"
            artifact.symlink_to(target)

            built = build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(built, artifact)
            self.assertFalse(artifact.is_symlink())
            self.assertEqual(target.read_text(encoding="utf-8"), "victim")
            with zipfile.ZipFile(artifact) as archive:
                self.assertIn("scrutinize-me/SKILL.md", set(archive.namelist()))

    def test_build_release_zip_rejects_symlinked_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            outside = root / "outside"
            outside.mkdir()
            output_dir = root / "dist-link"
            output_dir.symlink_to(outside, target_is_directory=True)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse(any(outside.iterdir()))

    def test_build_release_zip_rejects_output_dir_within_skill_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = skill_root / "references" / "nested-dist"

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("source", str(context.exception))
            self.assertFalse(output_dir.exists())

    def test_build_release_zip_cleans_up_failed_artifact_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            artifact = root / "dist" / f"{SKILL_NAME}-{__version__}.zip"

            def partial_iter(_: Path | None = None):
                yield (skill_root / "SKILL.md", "SKILL.md")
                raise RuntimeError("boom")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.iter_shippable_skill_files",
                    side_effect=partial_iter,
                ):
                    with self.assertRaises(RuntimeError):
                        build_release_zip(output_dir=root / "dist", version=__version__)

            self.assertFalse(artifact.exists())

    def test_build_release_zip_accepts_matching_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact = build_release_zip(
                output_dir=Path(tmp_dir),
                version=__version__,
                release_tag=f"v{__version__}",
            )

            self.assertTrue(artifact.exists())

    def test_build_release_zip_rejects_mismatched_release_tag_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaises(ValueError):
                build_release_zip(
                    output_dir=output_dir,
                    version=__version__,
                    release_tag="v9.9.9",
                )

            self.assertFalse(any(output_dir.iterdir()))

    def test_build_release_zip_preserves_existing_artifact_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            output_dir.mkdir()
            artifact = output_dir / f"{SKILL_NAME}-{__version__}.zip"
            artifact.write_text("old", encoding="utf-8")
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            temp_artifact = output_dir / f".{SKILL_NAME}-{__version__}-{fixed_uuid.hex}.zip.tmp"

            with mock.patch("scrutinize_me_skill.builder.uuid4", return_value=fixed_uuid):
                with mock.patch(
                    "scrutinize_me_skill.builder.replace_entry",
                    side_effect=RuntimeError("replace failed"),
                ):
                    with self.assertRaises(RuntimeError):
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(artifact.read_text(encoding="utf-8"), "old")
            self.assertFalse(temp_artifact.exists())

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

    def test_materialize_skill_restores_after_keyboard_interrupt_during_staging_rename(self) -> None:
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
            state_uuid = uuid.UUID("00000000000000000000000000000003")
            uuid_sequence = [stage_uuid, backup_uuid, state_uuid]
            staging_name = f".{SKILL_NAME}-staging-{stage_uuid.hex}"
            staging_path = export_root / staging_name
            backup_path = export_root / f".{SKILL_NAME}-backup-{backup_uuid.hex}"

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_rename(_: int, source_name: str, target_name: str) -> None:
                if source_name == staging_name and target_name == SKILL_NAME:
                    raise KeyboardInterrupt()
                (export_root / source_name).rename(export_root / target_name)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.uuid4", side_effect=fake_uuid4):
                    with mock.patch(
                        "scrutinize_me_skill.builder.rename_entry", side_effect=fake_rename
                    ):
                        with self.assertRaises(KeyboardInterrupt):
                            materialize_skill(export_root, force=True)

            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())
            self.assertFalse(staging_path.exists())
            self.assertFalse(backup_path.exists())

    def test_materialize_skill_recovers_previous_interrupted_export_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            backup_path = export_root / f".{SKILL_NAME}-backup-deadbeef"
            staging_path = export_root / f".{SKILL_NAME}-staging-feedface"
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            destination = export_root / SKILL_NAME
            backup_path.mkdir()
            staging_path.mkdir()
            existing_marker = backup_path / "existing.txt"
            existing_marker.write_text("keep", encoding="utf-8")
            state_path.write_text(
                json.dumps(
                    {
                        "backup_dir": backup_path.name,
                        "staging_dir": staging_path.name,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(FileExistsError):
                    materialize_skill(export_root)

            self.assertTrue(destination.exists())
            self.assertTrue((destination / "existing.txt").exists())
            self.assertFalse(backup_path.exists())
            self.assertFalse(staging_path.exists())
            self.assertFalse(state_path.exists())

    def test_materialize_skill_rejects_export_state_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / SKILL_NAME
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            victim = root / "victim"
            victim.mkdir()
            marker = victim / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            state_path.write_text(
                json.dumps({"backup_dir": None, "staging_dir": "../victim"}),
                encoding="utf-8",
            )

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("export state", str(context.exception))
            self.assertTrue(victim.exists())
            self.assertTrue(marker.exists())
            self.assertTrue(state_path.exists())
            self.assertFalse(destination.exists())

    def test_materialize_skill_rejects_absolute_path_with_existing_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            outside = root / "outside"
            outside.mkdir()
            link_parent = root / "link-parent"
            link_parent.symlink_to(outside, target_is_directory=True)
            existing = link_parent / "existing"
            existing.mkdir()
            export_root = (existing / "exports").absolute()

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / "existing" / "exports").exists())

    def test_build_release_zip_rejects_absolute_path_with_existing_symlink_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            outside = root / "outside"
            outside.mkdir()
            link_parent = root / "dist-link"
            link_parent.symlink_to(outside, target_is_directory=True)
            existing = link_parent / "existing"
            existing.mkdir()
            output_dir = (existing / "dist").absolute()

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / "existing" / "dist").exists())

    def test_materialize_skill_cleans_up_when_state_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.write_export_state",
                    side_effect=RuntimeError("state write failed"),
                ):
                    with self.assertRaises(RuntimeError):
                        materialize_skill(export_root)

            self.assertTrue(export_root.exists())
            self.assertEqual(
                sorted(path.name for path in export_root.iterdir()),
                [f".{SKILL_NAME}-export.lock"],
            )

    def test_materialize_skill_rejects_symlinked_export_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            victim = root / "outside-state.json"
            victim.write_text("[]", encoding="utf-8")
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            state_path.symlink_to(victim)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("export state", str(context.exception))
            self.assertEqual(victim.read_text(encoding="utf-8"), "[]")

    def test_materialize_skill_quarantines_malformed_export_state_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            state_path.write_text("{not-json", encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_materialize_skill_quarantines_non_mapping_export_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            state_path.write_text(json.dumps(["bad-state"]), encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_materialize_skill_quarantines_future_export_state_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "state_version": 999,
                        "staging_dir": None,
                        "backup_dir": None,
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_materialize_skill_quarantines_oversized_export_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            state_path = export_root / f".{SKILL_NAME}-export-state.json"

            from scrutinize_me_skill import builder as builder_module

            payload = {
                "state_version": 1,
                "staging_dir": None,
                "backup_dir": None,
                "padding": "a" * builder_module.EXPORT_STATE_MAX_BYTES,
            }
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_recover_export_state_reads_until_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_root = Path(tmp_dir)
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            payload = {"state_version": 1, "staging_dir": None, "backup_dir": None}
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            from scrutinize_me_skill import builder as builder_module

            data = state_path.read_bytes()
            chunks = [data[:5], data[5:10], data[10:], b""]
            read_calls = 0

            def short_read(_: int, __: int) -> bytes:
                nonlocal read_calls
                read_calls += 1
                if chunks:
                    return chunks.pop(0)
                return b""

            with builder_module.opened_directory(export_root, label="Export target root") as root_fd:
                with mock.patch("scrutinize_me_skill.builder.os.read", side_effect=short_read):
                    builder_module.recover_export_state(export_root, root_fd, SKILL_NAME)

            self.assertGreater(read_calls, 1)
            self.assertFalse(state_path.exists())
    def test_materialize_skill_does_not_follow_state_file_symlink_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            state_path = export_root / f".{SKILL_NAME}-export-state.json"
            victim = root / "victim.txt"
            victim.write_text("safe", encoding="utf-8")
            real_write_text = Path.write_text

            def fake_write_text(self: Path, data: str, *args, **kwargs):
                if self == state_path:
                    export_root.mkdir(parents=True, exist_ok=True)
                    if state_path.exists() or state_path.is_symlink():
                        state_path.unlink()
                    state_path.symlink_to(victim)
                return real_write_text(self, data, *args, **kwargs)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch.object(Path, "write_text", fake_write_text):
                    materialize_skill(export_root)

            self.assertEqual(victim.read_text(encoding="utf-8"), "safe")

    def test_materialize_skill_accepts_tmp_alias_target_root(self) -> None:
        if not Path("/tmp").exists():
            self.skipTest("requires /tmp")

        with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = Path("/tmp") / root.name / "exports"

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                skill_dir = materialize_skill(export_root)

            self.assertEqual(skill_dir, export_root / SKILL_NAME)
            self.assertTrue((Path(tmp_dir) / "exports" / SKILL_NAME / "SKILL.md").exists())

    def test_materialize_skill_rejects_unsupported_platform_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.os.name", "nt"):
                    with self.assertRaises(RuntimeError) as context:
                        materialize_skill(export_root)

            self.assertIn("POSIX", str(context.exception))
            self.assertFalse(export_root.exists())

    def test_build_release_zip_accepts_tmp_alias_output_dir(self) -> None:
        if not Path("/tmp").exists():
            self.skipTest("requires /tmp")

        with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir:
            output_dir = Path("/tmp") / Path(tmp_dir).name / "dist"
            artifact = build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(artifact, output_dir / f"{SKILL_NAME}-{__version__}.zip")
            self.assertTrue((Path(tmp_dir) / "dist" / artifact.name).exists())

    def test_build_release_zip_rejects_unsupported_platform_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "dist"

            with mock.patch("scrutinize_me_skill.builder.os.name", "nt"):
                with self.assertRaises(RuntimeError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("POSIX", str(context.exception))
            self.assertFalse(output_dir.exists())

    def test_build_release_zip_rejects_explicit_version_override_mismatch_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version="1.2.3")

            self.assertEqual(list(output_dir.iterdir()), [])

    def test_materialize_skill_rejects_concurrent_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            started = threading.Event()
            release_first = threading.Event()
            failures: list[BaseException] = []

            from scrutinize_me_skill import builder as builder_module

            real_write_export_state = builder_module.write_export_state

            def gated_write(*args, **kwargs):
                if not started.is_set():
                    started.set()
                    release_first.wait(timeout=5)
                return real_write_export_state(*args, **kwargs)

            def first_export() -> None:
                try:
                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root
                    ):
                        with mock.patch(
                            "scrutinize_me_skill.builder.write_export_state", side_effect=gated_write
                        ):
                            materialize_skill(export_root, force=True)
                except BaseException as exc:
                    failures.append(exc)

            worker = threading.Thread(target=first_export)
            worker.start()
            self.assertTrue(started.wait(timeout=5))

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with self.assertRaises(FileExistsError):
                    materialize_skill(export_root, force=True)

            release_first.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue((export_root / SKILL_NAME / "SKILL.md").exists())

    def test_materialize_skill_rejects_symlink_swap_after_payload_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            target = skill_root / "agents" / "openai.yaml"

            from scrutinize_me_skill import builder as builder_module

            real_iter = builder_module.iter_shippable_skill_files

            def swapped(source_root: Path | None = None):
                shipped = real_iter(source_root)
                target.unlink()
                target.symlink_to(victim)
                return shipped

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.iter_shippable_skill_files", side_effect=swapped
                ):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertEqual(victim.read_text(encoding="utf-8"), "SECRET")
            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_build_release_zip_rejects_symlink_swap_after_payload_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            target = skill_root / "agents" / "openai.yaml"

            from scrutinize_me_skill import builder as builder_module

            real_iter = builder_module.iter_shippable_skill_files

            def swapped(source_root: Path | None = None):
                shipped = real_iter(source_root)
                target.unlink()
                target.symlink_to(victim)
                return shipped

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.iter_shippable_skill_files", side_effect=swapped
                ):
                    with self.assertRaises(ValueError):
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(victim.read_text(encoding="utf-8"), "SECRET")
            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_materialize_skill_rejects_target_root_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            redirect = root / "redirect"
            redirect.mkdir()
            from scrutinize_me_skill import builder as builder_module

            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == export_root.name and not export_root.exists() and not export_root.is_symlink():
                    export_root.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.os.mkdir", side_effect=swapped_mkdir):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse(any(redirect.iterdir()))

    def test_build_release_zip_rejects_output_dir_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            redirect = root / "redirect"
            redirect.mkdir()
            from scrutinize_me_skill import builder as builder_module

            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == output_dir.name and not output_dir.exists() and not output_dir.is_symlink():
                    output_dir.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch("scrutinize_me_skill.builder.os.mkdir", side_effect=swapped_mkdir):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse(any(redirect.iterdir()))

    def test_build_release_zip_rejects_blank_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version=__version__, release_tag="")

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version=__version__, release_tag="   ")

            self.assertEqual(list(output_dir.iterdir()), [])

    def test_materialize_skill_rejects_ancestor_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "outer" / "inner"
            redirect = root / "redirect"
            redirect.mkdir()
            ancestor = root / "outer"
            from scrutinize_me_skill import builder as builder_module

            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == ancestor.name and not ancestor.exists() and not ancestor.is_symlink():
                    ancestor.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.os.mkdir", side_effect=swapped_mkdir):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse((redirect / "inner" / SKILL_NAME).exists())

    def test_build_release_zip_rejects_ancestor_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "outer" / "dist"
            redirect = root / "redirect"
            redirect.mkdir()
            ancestor = root / "outer"
            from scrutinize_me_skill import builder as builder_module

            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == ancestor.name and not ancestor.exists() and not ancestor.is_symlink():
                    ancestor.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch("scrutinize_me_skill.builder.os.mkdir", side_effect=swapped_mkdir):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse((redirect / "dist" / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_materialize_skill_retries_short_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"

            from scrutinize_me_skill import builder as builder_module

            real_write = builder_module.os.write
            short_write_done = False

            def short_write(fd: int, data: bytes) -> int:
                nonlocal short_write_done
                if not short_write_done and data == b"model: gpt\n":
                    short_write_done = True
                    real_write(fd, data[:5])
                    return 5
                return real_write(fd, data)

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch("scrutinize_me_skill.builder.os.write", side_effect=short_write):
                    materialize_skill(export_root)

            self.assertTrue(short_write_done)
            self.assertEqual(
                (export_root / SKILL_NAME / "agents" / "openai.yaml").read_text(encoding="utf-8"),
                "model: gpt\n",
            )

    def test_materialize_skill_rejects_in_place_source_edit_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            target = skill_root / "agents" / "openai.yaml"

            from scrutinize_me_skill import builder as builder_module

            real_snapshot = builder_module.snapshot_shippable_skill_files

            def rewritten(source_root: Path | None = None):
                snapshots = real_snapshot(source_root)
                target.write_text("changed\n", encoding="utf-8")
                return snapshots

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.snapshot_shippable_skill_files",
                    side_effect=rewritten,
                ):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_build_release_zip_rejects_in_place_source_edit_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
            target = skill_root / "agents" / "openai.yaml"

            from scrutinize_me_skill import builder as builder_module

            real_snapshot = builder_module.snapshot_shippable_skill_files

            def rewritten(source_root: Path | None = None):
                snapshots = real_snapshot(source_root)
                target.write_text("changed\n", encoding="utf-8")
                return snapshots

            with mock.patch("scrutinize_me_skill.builder.skill_source_dir", return_value=skill_root):
                with mock.patch(
                    "scrutinize_me_skill.builder.snapshot_shippable_skill_files",
                    side_effect=rewritten,
                ):
                    with self.assertRaises(ValueError):
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())


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
        expected = set(REQUIRED_SKILL_FILES) | {
            "agents/config/override.json",
            "references/guide/deck.md",
        }

        self.assertEqual(relative_names, expected)


if __name__ == "__main__":
    unittest.main()
