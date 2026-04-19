import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zipfile


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill import builder as builder_module
from scrutinize_me_skill.builder import build_release_zip
from scrutinize_me_skill.manifest import REQUIRED_SKILL_FILES, SKILL_NAME


class BuildReleaseZipTests(unittest.TestCase):
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

    def test_build_release_zip_contains_skill_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact = build_release_zip(output_dir=Path(tmp_dir), version=__version__)

            self.assertEqual(artifact.name, f"{SKILL_NAME}-{__version__}.zip")
            self.assertTrue(artifact.exists())

            with zipfile.ZipFile(artifact) as archive:
                archive_names = set(archive.namelist())

            self.assertIn(f"{SKILL_NAME}/SKILL.md", archive_names)
            self.assertIn(f"{SKILL_NAME}/agents/openai.yaml", archive_names)
            self.assertIn(f"{SKILL_NAME}/evals/evals.json", archive_names)
            self.assertIn(f"{SKILL_NAME}/references/reviewer-personas.md", archive_names)
            self.assertIn(f"{SKILL_NAME}/references/orchestrator-playbook.md", archive_names)
            self.assertIn(f"{SKILL_NAME}/references/output-schema.md", archive_names)
            self.assertIn(f"{SKILL_NAME}/references/review-template.md", archive_names)

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

    def test_build_release_zip_creates_owner_only_directories_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / "outer" / "dist"
            original_umask = os.umask(0)
            try:
                build_release_zip(output_dir=output_dir, version=__version__)
            finally:
                os.umask(original_umask)

            self._assert_owner_only_dir(output_dir)
            self._assert_owner_only_dir(output_dir.parent)

    def test_build_release_zip_fsyncs_artifact_and_output_dir_before_reporting_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            temp_artifact_name = f".{SKILL_NAME}-{__version__}-{fixed_uuid.hex}.zip.tmp"
            final_artifact_name = f"{SKILL_NAME}-{__version__}.zip"
            events: list[str] = []
            real_replace = builder_module.replace_entry
            real_fsync = builder_module.os.fsync

            def label_fd(fd: int) -> str:
                fd_stat = os.fstat(fd)
                candidates = [output_dir]
                if output_dir.exists():
                    candidates.extend(sorted(output_dir.rglob("*")))
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

            def record_replace(root_fd: int, source_name: str, target_name: str) -> None:
                events.append(f"replace:{source_name}->{target_name}")
                real_replace(root_fd, source_name, target_name)

            with mock.patch.object(builder_module, "uuid4", return_value=fixed_uuid):
                with mock.patch.object(builder_module.os, "fsync", side_effect=record_fsync):
                    with mock.patch.object(builder_module, "replace_entry", side_effect=record_replace):
                        artifact = build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(artifact, output_dir / final_artifact_name)
            temp_event = f"fsync:dist/{temp_artifact_name}"
            replace_event = f"replace:{temp_artifact_name}->{final_artifact_name}"
            self.assertIn(temp_event, events)
            self.assertIn(replace_event, events)
            self.assertLess(events.index(temp_event), events.index(replace_event))
            self.assertTrue(
                any(index > events.index(replace_event) and events[index] == "fsync:dist" for index in range(len(events)))
            )

    def test_build_release_zip_rejects_missing_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)

            for relative in REQUIRED_SKILL_FILES:
                with self.subTest(relative=relative):
                    skill_root = self._make_skill_root(root / relative.replace("/", "_"))
                    output_dir = root / "dist" / relative.replace("/", "_")
                    (skill_root / relative).unlink()

                    with mock.patch(
                        "scrutinize_me_skill.builder.skill_source_dir",
                        return_value=skill_root,
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
                self.assertIn(f"{SKILL_NAME}/SKILL.md", set(archive.namelist()))

    def test_build_release_zip_rejects_symlinked_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            outside = root / "outside"
            outside.mkdir()
            output_dir = root / "dist-link"
            output_dir.symlink_to(outside, target_is_directory=True)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse(any(outside.iterdir()))

    def test_build_release_zip_rejects_output_dir_within_skill_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = skill_root / "references" / "nested-dist"

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("source", str(context.exception))
            self.assertFalse(output_dir.exists())

    def test_build_release_zip_rejects_symlink_swap_after_payload_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
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
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(victim.read_text(encoding="utf-8"), "SECRET")
            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_build_release_zip_rejects_hard_linked_payload_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
            victim = root / "victim.txt"
            victim.write_text("SECRET", encoding="utf-8")
            linked = skill_root / "references" / "leak.md"
            os.link(victim, linked)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_build_release_zip_rejects_in_place_source_edit_after_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
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
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_build_release_zip_rejects_in_place_source_edit_during_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            output_dir = root / "dist"
            target = skill_root / "agents" / "openai.yaml"
            target_label = "agents/openai.yaml"
            rewritten = False
            real_stream = builder_module.stream_fd_to_writer

            def rewrite_during_stream(source_fd: int, writer, *, label: str) -> None:
                nonlocal rewritten
                if label == target_label and not rewritten:
                    rewritten = True
                    target.write_text("changed during stream\n", encoding="utf-8")
                real_stream(source_fd, writer, label=label)

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(
                    builder_module,
                    "stream_fd_to_writer",
                    side_effect=rewrite_during_stream,
                ):
                    with self.assertRaises(ValueError):
                        build_release_zip(output_dir=output_dir, version=__version__)

            self.assertTrue(rewritten)
            self.assertFalse((output_dir / f"{SKILL_NAME}-{__version__}.zip").exists())

    def test_build_release_zip_cleans_up_failed_artifact_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            artifact = root / "dist" / f"{SKILL_NAME}-{__version__}.zip"

            def partial_iter(_: Path | None = None):
                yield (skill_root / "SKILL.md", "SKILL.md")
                raise RuntimeError("boom")

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(
                    builder_module,
                    "iter_shippable_skill_files",
                    side_effect=partial_iter,
                ):
                    with self.assertRaises(RuntimeError):
                        build_release_zip(output_dir=root / "dist", version=__version__)

            self.assertFalse(artifact.exists())

    def test_build_release_zip_cleans_up_temp_artifact_after_mid_stream_writer_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            skill_root = self._make_skill_root(root / "source")
            large_path = skill_root / "references" / "large.txt"
            large_path.write_bytes(b"0123456789abcdef" * 5000)
            output_dir = root / "dist"
            artifact = output_dir / f"{SKILL_NAME}-{__version__}.zip"
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            temp_artifact = output_dir / f".{SKILL_NAME}-{__version__}-{fixed_uuid.hex}.zip.tmp"
            large_member = f"{SKILL_NAME}/references/large.txt"
            target_writes = 0
            real_open = builder_module.ZipFile.open

            class FailingWriter:
                def __init__(self, inner) -> None:
                    self._inner = inner

                def __enter__(self):
                    self._inner.__enter__()
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return self._inner.__exit__(exc_type, exc, tb)

                def write(self, data: bytes) -> int:
                    nonlocal target_writes
                    target_writes += 1
                    if target_writes == 2:
                        raise OSError("stream write failed")
                    return self._inner.write(data)

            def failing_open(self, name, mode="r", *args, **kwargs):
                inner = real_open(self, name, mode, *args, **kwargs)
                if name == large_member:
                    return FailingWriter(inner)
                return inner

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with mock.patch.object(builder_module, "uuid4", return_value=fixed_uuid):
                    with mock.patch("scrutinize_me_skill.builder.ZipFile.open", new=failing_open):
                        with self.assertRaises(OSError) as context:
                            build_release_zip(output_dir=output_dir, version=__version__)

            self.assertEqual(str(context.exception), "stream write failed")
            self.assertGreater(target_writes, 1)
            self.assertFalse(artifact.exists())
            self.assertFalse(temp_artifact.exists())

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

    def test_build_release_zip_rejects_explicit_version_override_mismatch_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version="1.2.3")

            self.assertEqual(list(output_dir.iterdir()), [])

    def test_build_release_zip_rejects_blank_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version=__version__, release_tag="")

            with self.assertRaises(ValueError):
                build_release_zip(output_dir=output_dir, version=__version__, release_tag="   ")

            self.assertEqual(list(output_dir.iterdir()), [])

    def test_build_release_zip_preserves_existing_artifact_when_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            output_dir.mkdir()
            artifact = output_dir / f"{SKILL_NAME}-{__version__}.zip"
            artifact.write_text("old", encoding="utf-8")
            fixed_uuid = uuid.UUID("00000000000000000000000000000001")
            temp_artifact = output_dir / f".{SKILL_NAME}-{__version__}-{fixed_uuid.hex}.zip.tmp"

            with mock.patch.object(builder_module, "uuid4", return_value=fixed_uuid):
                with mock.patch.object(
                    builder_module,
                    "replace_entry",
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                artifact = build_release_zip(output_dir=root / "dist", version=__version__)

            with zipfile.ZipFile(artifact) as archive:
                archive_names = set(archive.namelist())

            self.assertIn(f"{SKILL_NAME}/SKILL.md", archive_names)
            self.assertNotIn(f"{SKILL_NAME}/notes.txt", archive_names)
            self.assertNotIn(f"{SKILL_NAME}/references/.hidden.md", archive_names)
            self.assertNotIn(f"{SKILL_NAME}/references/__pycache__/cached.py", archive_names)
            self.assertNotIn(f"{SKILL_NAME}/references/compiled.pyc", archive_names)

    def test_build_release_zip_rejects_file_output_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "dist"
            output_path.write_text("not a dir", encoding="utf-8")

            with self.assertRaises(ValueError) as context:
                build_release_zip(output_dir=output_path, version=__version__)

        self.assertIn("not a directory", str(context.exception))

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

            with mock.patch(
                "scrutinize_me_skill.builder.skill_source_dir",
                return_value=skill_root,
            ):
                with self.assertRaises(ValueError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("symlink", str(context.exception))
            self.assertFalse((outside / "existing" / "dist").exists())

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

            with mock.patch.object(builder_module.os, "name", "nt"):
                with self.assertRaises(RuntimeError) as context:
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertIn("POSIX", str(context.exception))
            self.assertFalse(output_dir.exists())

    def test_build_release_zip_rejects_output_dir_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "dist"
            redirect = root / "redirect"
            redirect.mkdir()
            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == output_dir.name and not output_dir.exists() and not output_dir.is_symlink():
                    output_dir.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch.object(builder_module.os, "mkdir", side_effect=swapped_mkdir):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse(any(redirect.iterdir()))

    def test_build_release_zip_rejects_ancestor_symlink_swap_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output_dir = root / "outer" / "dist"
            redirect = root / "redirect"
            redirect.mkdir()
            ancestor = root / "outer"
            real_mkdir = builder_module.os.mkdir

            def swapped_mkdir(name: str, mode: int = 0o777, *, dir_fd: int | None = None):
                if name == ancestor.name and not ancestor.exists() and not ancestor.is_symlink():
                    ancestor.symlink_to(redirect, target_is_directory=True)
                return real_mkdir(name, mode, dir_fd=dir_fd)

            with mock.patch.object(builder_module.os, "mkdir", side_effect=swapped_mkdir):
                with self.assertRaises(ValueError):
                    build_release_zip(output_dir=output_dir, version=__version__)

            self.assertFalse((redirect / "dist" / f"{SKILL_NAME}-{__version__}.zip").exists())


if __name__ == "__main__":
    unittest.main()
