import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import builder as builder_module
from scrutinize_me_skill.builder import materialize_skill
from scrutinize_me_skill import builder_state
from scrutinize_me_skill.manifest import SKILL_NAME


class BuilderStateTests(unittest.TestCase):
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

    def test_materialize_skill_recovers_previous_interrupted_export_state_after_short_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            backup_path = export_root / f".{SKILL_NAME}-backup-deadbeef"
            staging_path = export_root / f".{SKILL_NAME}-staging-feedface"
            state_path = export_root / builder_state.export_state_name()
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
                        "padding": "a" * 70000,
                    }
                ),
                encoding="utf-8",
            )
            real_read = builder_module.os.read

            def short_read(fd: int, size: int) -> bytes:
                return real_read(fd, min(size, 1024))

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module.os, "read", side_effect=short_read):
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
            state_path = export_root / builder_state.export_state_name()
            victim = root / "victim"
            victim.mkdir()
            marker = victim / "marker.txt"
            marker.write_text("keep", encoding="utf-8")
            state_path.write_text(
                json.dumps({"backup_dir": None, "staging_dir": "../victim"}),
                encoding="utf-8",
            )

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    materialize_skill(export_root)

            self.assertIn("export state", str(context.exception))
            self.assertTrue(victim.exists())
            self.assertTrue(marker.exists())
            self.assertTrue(state_path.exists())
            self.assertFalse(destination.exists())

    def test_materialize_skill_cleans_up_when_state_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch(
                    "scrutinize_me_skill.builder_state.write_export_state",
                    side_effect=RuntimeError("state write failed"),
                ):
                    with self.assertRaises(RuntimeError):
                        materialize_skill(export_root)

            self.assertTrue(export_root.exists())
            self.assertEqual(
                sorted(path.name for path in export_root.iterdir()),
                [f".{SKILL_NAME}-export.lock"],
            )

    def test_materialize_skill_does_not_follow_state_file_symlink_on_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            state_name = builder_state.export_state_name()
            state_path = export_root / state_name
            victim = root / "victim.txt"
            victim.write_text("safe", encoding="utf-8")
            real_replace_entry = builder_module.replace_entry

            def swapped_replace(root_fd: int, source_name: str, target_name: str) -> None:
                if target_name == state_name:
                    export_root.mkdir(parents=True, exist_ok=True)
                    if state_path.exists() or state_path.is_symlink():
                        state_path.unlink()
                    state_path.symlink_to(victim)
                real_replace_entry(root_fd, source_name, target_name)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "replace_entry", side_effect=swapped_replace):
                    materialize_skill(export_root)

            self.assertEqual(victim.read_text(encoding="utf-8"), "safe")

    def test_materialize_skill_rejects_symlinked_export_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            victim = root / "outside-state.json"
            victim.write_text("[]", encoding="utf-8")
            state_path = export_root / builder_state.export_state_name()
            state_path.symlink_to(victim)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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
            state_path = export_root / builder_state.export_state_name()
            state_path.write_text("{not-json", encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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
            state_path = export_root / builder_state.export_state_name()
            state_path.write_text(json.dumps(["bad-state"]), encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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
            state_path = export_root / builder_state.export_state_name()
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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
            state_path = export_root / builder_state.export_state_name()
            payload = {
                "state_version": 1,
                "staging_dir": None,
                "backup_dir": None,
                "padding": "a" * builder_state.EXPORT_STATE_MAX_BYTES,
            }
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_materialize_skill_quarantines_non_regular_export_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            export_root.mkdir(parents=True)
            state_path = export_root / builder_state.export_state_name()
            state_path.mkdir()

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                skill_dir = materialize_skill(export_root)

            self.assertTrue((skill_dir / "SKILL.md").exists())
            self.assertFalse(state_path.exists())
            quarantined = list(export_root.glob(f".{SKILL_NAME}-export-state.invalid-*.json"))
            self.assertEqual(len(quarantined), 1)

    def test_materialize_skill_rejects_concurrent_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            export_root = root / "exports"
            started = threading.Event()
            release_first = threading.Event()
            failures: list[BaseException] = []
            real_write_export_state = builder_state.write_export_state

            def gated_write(*args, **kwargs):
                if not started.is_set():
                    started.set()
                    release_first.wait(timeout=5)
                return real_write_export_state(*args, **kwargs)

            def first_export() -> None:
                try:
                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir",
                        return_value=skill_root,
                    ):
                        with mock.patch(
                            "scrutinize_me_skill.builder_state.write_export_state",
                            side_effect=gated_write,
                        ):
                            materialize_skill(export_root, force=True)
                except BaseException as exc:
                    failures.append(exc)

            worker = threading.Thread(target=first_export)
            worker.start()
            self.assertTrue(started.wait(timeout=5))

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(FileExistsError):
                    materialize_skill(export_root, force=True)

            release_first.set()
            worker.join(timeout=5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertTrue((export_root / SKILL_NAME / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
