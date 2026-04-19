import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import builder_fs, builder_transfer
from scrutinize_me_skill.manifest import REQUIRED_SKILL_FILES


class BuilderTransferTests(unittest.TestCase):
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
        (skill_root / "references" / "reviewer-personas.md").write_text(
            "# Personas\n", encoding="utf-8"
        )
        guide_dir = skill_root / "references" / "guide"
        guide_dir.mkdir(parents=True)
        (guide_dir / "deck.md").write_text("guide\n", encoding="utf-8")
        (skill_root / "references" / "orchestrator-playbook.md").write_text(
            "# Playbook\n", encoding="utf-8"
        )
        (skill_root / "references" / "review-template.md").write_text(
            "# Template\n", encoding="utf-8"
        )
        (skill_root / "references" / "output-schema.md").write_text("schema\n", encoding="utf-8")
        (skill_root / "references" / ".hidden").write_text("secret\n", encoding="utf-8")
        (skill_root / "notes.txt").write_text("ignore\n", encoding="utf-8")
        return skill_root

    def test_iter_shippable_skill_files_returns_exact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = self._create_skill_layout(Path(tmp_dir))
            shipped = builder_transfer.iter_shippable_skill_files(skill_root)

        relative_names = {relative for _, relative in shipped}
        expected = set(REQUIRED_SKILL_FILES) | {
            "agents/config/override.json",
            "references/guide/deck.md",
        }
        self.assertEqual(relative_names, expected)

    def test_stream_fd_to_fd_copies_large_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source.bin"
            destination = root / "destination.bin"
            payload = (b"0123456789abcdef" * 8192) + b"tail"
            source.write_bytes(payload)

            source_fd = os.open(source, os.O_RDONLY)
            destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                builder_transfer.stream_fd_to_fd(source_fd, destination_fd, label="payload.bin")
            finally:
                os.close(source_fd)
                os.close(destination_fd)

            self.assertEqual(destination.read_bytes(), payload)

    def test_stream_fd_to_writer_copies_large_payload_in_multiple_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            source = root / "source.bin"
            payload = (b"abcdefghijklmnopqrstuvwxyz" * 4096) + b"tail"
            source.write_bytes(payload)
            writes: list[int] = []
            captured = bytearray()

            class RecordingWriter:
                def write(self, data: bytes) -> int:
                    chunk = bytes(data)
                    writes.append(len(chunk))
                    captured.extend(chunk)
                    return len(chunk)

            source_fd = os.open(source, os.O_RDONLY)
            try:
                builder_transfer.stream_fd_to_writer(
                    source_fd,
                    RecordingWriter(),
                    label="payload.bin",
                )
            finally:
                os.close(source_fd)

            self.assertGreater(len(writes), 1)
            self.assertEqual(bytes(captured), payload)

    def test_stream_fd_to_writer_rejects_writer_returning_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "source.bin"
            source.write_bytes(b"payload")

            class NoneWriter:
                def write(self, data: bytes):
                    return None

            source_fd = os.open(source, os.O_RDONLY)
            try:
                with self.assertRaises(OSError):
                    builder_transfer.stream_fd_to_writer(
                        source_fd,
                        NoneWriter(),
                        label="payload.bin",
                    )
            finally:
                os.close(source_fd)

    def test_opened_snapshotted_file_rejects_source_symlink_swap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._create_skill_layout(root)
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            target = skill_root / "agents" / "openai.yaml"
            snapshot = next(
                item
                for item in builder_transfer.snapshot_shippable_skill_files(skill_root)
                if item.relative == "agents/openai.yaml"
            )

            target.unlink()
            target.symlink_to(victim)

            with self.assertRaises(OSError):
                with builder_transfer.opened_snapshotted_file(snapshot):
                    pass

            self.assertEqual(victim.read_text(encoding="utf-8"), "SECRET")

    def test_opened_snapshotted_file_rejects_in_place_source_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = self._create_skill_layout(Path(tmp_dir))
            target = skill_root / "agents" / "openai.yaml"
            snapshot = next(
                item
                for item in builder_transfer.snapshot_shippable_skill_files(skill_root)
                if item.relative == "agents/openai.yaml"
            )

            target.write_text("changed\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                with builder_transfer.opened_snapshotted_file(snapshot):
                    pass

    def test_write_all_retries_short_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            destination = Path(tmp_dir) / "destination.txt"
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            real_write = builder_fs.os.write
            short_write_done = False

            def short_write(fd: int, data: bytes) -> int:
                nonlocal short_write_done
                if not short_write_done and bytes(data) == b"model: gpt\n":
                    short_write_done = True
                    real_write(fd, b"model")
                    return 5
                return real_write(fd, data)

            try:
                with mock.patch.object(builder_fs.os, "write", side_effect=short_write):
                    builder_fs.write_all(destination_fd, b"model: gpt\n", label="openai.yaml")
            finally:
                os.close(destination_fd)

            self.assertTrue(short_write_done)
            self.assertEqual(destination.read_text(encoding="utf-8"), "model: gpt\n")


if __name__ == "__main__":
    unittest.main()
