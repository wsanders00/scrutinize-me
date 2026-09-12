import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import uuid


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import builder as builder_module
from scrutinize_me_skill.builder import materialize_skill
from scrutinize_me_skill.manifest import REQUIRED_SKILL_FILES, SKILL_NAME


class MaterializeSkillTests(unittest.TestCase):
    def _assert_owner_only_dir(self, path: Path) -> None:
        mode = path.stat().st_mode & 0o777
        self.assertEqual(mode & 0o022, 0)

    def _make_skill_root(self, root: Path) -> Path:
        skill_root = root / SKILL_NAME
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

            self.assertEqual(skill_dir.name, SKILL_NAME)
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

    def test_materialize_skill_creates_owner_only_directories_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_root = Path(tmp_dir) / "outer" / "inner" / "exports"
            original_umask = os.umask(0)
            try:
                skill_dir = materialize_skill(export_root)
            finally:
                os.umask(original_umask)

            self._assert_owner_only_dir(export_root)
            self._assert_owner_only_dir(export_root.parent)
            self._assert_owner_only_dir(export_root.parent.parent)
            self._assert_owner_only_dir(skill_dir / "agents")
            self._assert_owner_only_dir(skill_dir / "references")

    def test_materialize_skill_creates_owner_only_staging_directory_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_root = Path(tmp_dir) / "exports"
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            staging_name = f".{SKILL_NAME}-staging-{fixed_uuid.hex}"
            staging_dir = export_root / staging_name
            original_umask = os.umask(0)
            try:
                with mock.patch.object(builder_module, "uuid4", return_value=fixed_uuid):
                    with mock.patch(
                        "scrutinize_me_skill.builder_transfer.copy_shippable_skill_tree",
                        side_effect=RuntimeError("boom"),
                    ):
                        with mock.patch.object(
                            builder_module,
                            "remove_entry_at",
                            side_effect=lambda *args, **kwargs: None,
                        ):
                            with self.assertRaises(RuntimeError):
                                materialize_skill(export_root)
            finally:
                os.umask(original_umask)

            self.assertTrue(staging_dir.exists())
            self._assert_owner_only_dir(staging_dir)

    def test_materialize_skill_rejects_existing_destination_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
            destination.mkdir(parents=True)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(FileExistsError) as context:
                    materialize_skill(export_root)

            self.assertIn("rerun with --force", str(context.exception))

    def test_materialize_skill_fsyncs_staging_and_root_before_reporting_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            stage_uuid = uuid.UUID("00000000000000000000000000000001")
            state_uuid = uuid.UUID("00000000000000000000000000000002")
            uuid_sequence = [stage_uuid, state_uuid]
            staging_name = f".{SKILL_NAME}-staging-{stage_uuid.hex}"
            events: list[str] = []
            real_rename = builder_module.rename_entry
            real_clear = builder_module.clear_export_state
            real_fsync = builder_module.os.fsync

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def label_fd(fd: int) -> str:
                fd_stat = os.fstat(fd)
                candidates = [export_root]
                if export_root.exists():
                    candidates.extend(sorted(export_root.rglob("*")))
                for candidate in candidates:
                    try:
                        candidate_stat = candidate.stat()
                    except FileNotFoundError:
                        continue
                    if (
                        candidate_stat.st_dev == fd_stat.st_dev
                        and candidate_stat.st_ino == fd_stat.st_ino
                        and stat.S_IFMT(candidate_stat.st_mode) == stat.S_IFMT(fd_stat.st_mode)
                    ):
                        return candidate.relative_to(root).as_posix()
                return f"fd:{fd}"

            def record_fsync(fd: int) -> None:
                events.append(f"fsync:{label_fd(fd)}")
                real_fsync(fd)

            def record_rename(root_fd: int, source_name: str, target_name: str) -> None:
                events.append(f"rename:{source_name}->{target_name}")
                real_rename(root_fd, source_name, target_name)

            def record_clear(root_fd: int) -> None:
                events.append("clear-export-state")
                real_clear(root_fd)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", side_effect=fake_uuid4):
                    with mock.patch.object(builder_module.os, "fsync", side_effect=record_fsync):
                        with mock.patch.object(builder_module, "rename_entry", side_effect=record_rename):
                            with mock.patch.object(
                                builder_module,
                                "clear_export_state",
                                side_effect=record_clear,
                            ):
                                skill_dir = materialize_skill(export_root)

            self.assertEqual(skill_dir, export_root / SKILL_NAME)
            staging_file_event = f"fsync:exports/{staging_name}/SKILL.md"
            staging_dir_event = f"fsync:exports/{staging_name}"
            publish_event = f"rename:{staging_name}->{SKILL_NAME}"
            clear_event = "clear-export-state"
            self.assertIn(staging_file_event, events)
            self.assertIn(staging_dir_event, events)
            self.assertIn(publish_event, events)
            self.assertIn(clear_event, events)
            self.assertLess(events.index(staging_file_event), events.index(publish_event))
            self.assertLess(events.index(staging_dir_event), events.index(publish_event))
            self.assertTrue(
                any(index > events.index(publish_event) and events[index] == "fsync:exports" for index in range(len(events)))
            )
            self.assertTrue(
                any(index > events.index(clear_event) and events[index] == "fsync:exports" for index in range(len(events)))
            )

    def test_materialize_skill_rejects_missing_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            missing_root = root / "source" / SKILL_NAME
            export_root = root / "exports"

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=missing_root,
            ):
                with self.assertRaises(FileNotFoundError):
                    materialize_skill(export_root)

            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_materialize_skill_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            for relative in REQUIRED_SKILL_FILES:
                with self.subTest(relative=relative):
                    skill_root = self._make_skill_root(root / relative.replace("/", "_"))
                    export_root = root / "exports" / relative.replace("/", "_")
                    (skill_root / relative).unlink()

                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir",
                        return_value=skill_root,
                    ):
                        with self.assertRaises(ValueError) as context:
                            materialize_skill(export_root)

                    self.assertIn(relative, str(context.exception))

    def test_materialize_skill_rejects_symlink_swap_after_payload_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            target = skill_root / "agents" / "openai.yaml"
            real_iter = builder_module.iter_shippable_skill_files

            def swapped(source_root: Path | None = None):
                shipped = real_iter(source_root)
                target.unlink()
                target.symlink_to(victim)
                return shipped

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "iter_shippable_skill_files", side_effect=swapped):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertEqual(victim.read_text(encoding="utf-8"), "SECRET")
            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_materialize_skill_rejects_hard_linked_payload_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            linked = skill_root / "references" / "leak.md"
            os.link(victim, linked)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError):
                    materialize_skill(export_root)

            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_materialize_skill_rejects_in_place_source_edit_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            target = skill_root / "agents" / "openai.yaml"
            real_snapshot = builder_module.snapshot_shippable_skill_files

            def rewritten(source_root: Path | None = None):
                snapshots = real_snapshot(source_root)
                target.write_text("changed\n", encoding="utf-8")
                return snapshots

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(
                    builder_module,
                    "snapshot_shippable_skill_files",
                    side_effect=rewritten,
                ):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_materialize_skill_rejects_in_place_source_edit_during_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            target = skill_root / "agents" / "openai.yaml"
            target_label = "agents/openai.yaml"
            rewritten = False
            real_stream = builder_module.stream_fd_to_fd

            def rewrite_during_stream(source_fd: int, destination_fd: int, *, label: str) -> None:
                nonlocal rewritten
                if label == target_label and not rewritten:
                    rewritten = True
                    target.write_text("changed during stream\n", encoding="utf-8")
                real_stream(source_fd, destination_fd, label=label)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(
                    builder_module,
                    "stream_fd_to_fd",
                    side_effect=rewrite_during_stream,
                ):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertTrue(rewritten)
            self.assertFalse((export_root / SKILL_NAME).exists())

    def test_materialize_skill_overwrites_existing_destination_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
            destination.mkdir(parents=True)
            stale_marker = destination / "stale.txt"
            stale_marker.write_text("stale", encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                skill_dir = materialize_skill(export_root, force=True)

            self.assertEqual(skill_dir, destination)
            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(stale_marker.exists())

    def test_materialize_skill_preserves_existing_export_on_copy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
            destination.mkdir(parents=True)
            existing_marker = destination / "existing.txt"
            existing_marker.write_text("keep", encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch(
                    "scrutinize_me_skill.builder_transfer.copy_shippable_skill_tree",
                    side_effect=RuntimeError("boom"),
                ):
                    with self.assertRaises(RuntimeError):
                        materialize_skill(export_root, force=True)

            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())

    def test_materialize_skill_cleans_up_staging_after_mid_stream_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            large_path = skill_root / "references" / "large.txt"
            large_path.write_bytes(b"0123456789abcdef" * 5000)
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
            destination.mkdir(parents=True)
            existing_marker = destination / "existing.txt"
            existing_marker.write_text("keep", encoding="utf-8")
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            staging_name = f".{SKILL_NAME}-staging-{fixed_uuid.hex}"
            large_label = "references/large.txt"
            write_counts: dict[str, int] = {}
            real_write_all = builder_module.write_all

            def fail_large_stream(fd: int, data: bytes, *, label: str) -> None:
                count = write_counts.get(label, 0) + 1
                write_counts[label] = count
                if label == large_label and count == 2:
                    raise OSError("stream write failed")
                real_write_all(fd, data, label=label)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", return_value=fixed_uuid):
                    with mock.patch.object(builder_module, "write_all", side_effect=fail_large_stream):
                        with self.assertRaises(OSError) as context:
                            materialize_skill(export_root, force=True)

            self.assertEqual(str(context.exception), "stream write failed")
            self.assertGreater(write_counts.get(large_label, 0), 1)
            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())
            self.assertFalse((export_root / staging_name).exists())

    def test_materialize_skill_restores_after_staging_rename_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", side_effect=fake_uuid4):
                    with mock.patch.object(builder_module, "rename_entry", side_effect=fake_rename):
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
            destination = export_root / SKILL_NAME
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", side_effect=fake_uuid4):
                    with mock.patch.object(builder_module, "rename_entry", side_effect=fake_rename):
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
            destination = export_root / SKILL_NAME
            destination.mkdir(parents=True)
            stale_marker = destination / "stale.txt"
            stale_marker.write_text("stale", encoding="utf-8")
            stage_uuid = uuid.UUID("00000000000000000000000000000001")
            backup_uuid = uuid.UUID("00000000000000000000000000000002")
            state_uuid = uuid.UUID("00000000000000000000000000000003")
            uuid_sequence = [stage_uuid, backup_uuid, state_uuid]
            backup_name = f".{SKILL_NAME}-backup-{backup_uuid.hex}"
            backup_path = export_root / backup_name
            real_remove_entry_at = builder_module.remove_entry_at

            def fake_uuid4() -> uuid.UUID:
                return uuid_sequence.pop(0)

            def fake_remove_entry_at(root_fd: int, name: str, *, ignore_errors: bool = False) -> None:
                if name == backup_name:
                    raise OSError("backup cleanup failed")
                real_remove_entry_at(root_fd, name, ignore_errors=ignore_errors)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", side_effect=fake_uuid4):
                    with mock.patch.object(
                        builder_module,
                        "remove_entry_at",
                        side_effect=fake_remove_entry_at,
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(skill_root)

            self.assertIn("source directory", str(context.exception))

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(root)

            self.assertIn("source directory", str(context.exception))

    def test_materialize_skill_rejects_source_nested_under_destination_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            target_root = root / "target"
            skill_root = self._make_skill_root(target_root / SKILL_NAME / "nested")
            source_marker = skill_root / "SKILL.md"
            destination = target_root / SKILL_NAME

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError):
                    materialize_skill(target_root, force=True)

            self.assertTrue(source_marker.exists())
            self.assertEqual(source_marker.read_text(encoding="utf-8"), "# Skill\n")
            self.assertTrue((destination / "nested" / SKILL_NAME / "SKILL.md").exists())
            self.assertEqual(list(target_root.glob(f".{SKILL_NAME}-*")), [])

    def test_materialize_skill_rejects_existing_destination_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / SKILL_NAME
            destination.write_text("not a directory", encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("not a directory", str(context.exception))

    def test_materialize_skill_rejects_existing_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / SKILL_NAME
            link_target = root / "link-target"
            link_target.mkdir()
            destination.symlink_to(link_target, target_is_directory=True)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))

    def test_materialize_skill_rejects_broken_destination_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            destination = export_root / SKILL_NAME
            destination.symlink_to(root / "missing-target", target_is_directory=True)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / SKILL_NAME).exists())

    def test_materialize_skill_preserves_relative_destination_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = Path("exports")
            cwd = Path.cwd()

            try:
                os.chdir(root)
                with mock.patch(
                    "scrutinize_me_skill.builder.skill_source_dir",
                    return_value=skill_root,
                ):
                    skill_dir = materialize_skill(export_root)
            finally:
                os.chdir(cwd)

            self.assertEqual(skill_dir, export_root / SKILL_NAME)
            self.assertFalse(skill_dir.is_absolute())
            self.assertTrue((root / skill_dir / "SKILL.md").exists())

    def test_materialize_skill_restores_after_keyboard_interrupt_during_staging_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            destination = export_root / SKILL_NAME
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", side_effect=fake_uuid4):
                    with mock.patch.object(builder_module, "rename_entry", side_effect=fake_rename):
                        with self.assertRaises(KeyboardInterrupt):
                            materialize_skill(export_root, force=True)

            self.assertTrue(destination.exists())
            self.assertTrue(existing_marker.exists())
            self.assertFalse(staging_path.exists())
            self.assertFalse(backup_path.exists())

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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / "existing" / "exports").exists())

    def test_materialize_skill_accepts_tmp_alias_target_root(self) -> None:
        if not Path("/tmp").exists():
            self.skipTest("requires /tmp")

        with tempfile.TemporaryDirectory(dir="/tmp") as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = Path("/tmp") / root.name / "exports"

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                skill_dir = materialize_skill(export_root)

            self.assertEqual(skill_dir, export_root / SKILL_NAME)
            self.assertTrue((Path(tmp_dir) / "exports" / SKILL_NAME / "SKILL.md").exists())

    def test_materialize_skill_rejects_unsupported_platform_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module.os, "name", "nt"):
                    with self.assertRaises(RuntimeError) as context:
                        materialize_skill(export_root)

            self.assertIn("POSIX", str(context.exception))
            self.assertFalse(export_root.exists())

    def test_materialize_skill_rejects_target_root_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            redirect = root / "redirect"
            redirect.mkdir()
            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == export_root.name and not export_root.exists() and not export_root.is_symlink():
                    export_root.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module.os, "mkdir", side_effect=swapped_mkdir):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse(any(redirect.iterdir()))

    def test_materialize_skill_rejects_ancestor_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "outer" / "inner"
            redirect = root / "redirect"
            redirect.mkdir()
            ancestor = root / "outer"
            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == ancestor.name and not ancestor.exists() and not ancestor.is_symlink():
                    ancestor.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module.os, "mkdir", side_effect=swapped_mkdir):
                    with self.assertRaises(ValueError):
                        materialize_skill(export_root)

            self.assertFalse((redirect / "inner" / SKILL_NAME).exists())


if __name__ == "__main__":
    unittest.main()
