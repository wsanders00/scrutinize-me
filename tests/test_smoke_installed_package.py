import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SMOKE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "smoke_installed_package.py"
SMOKE_SPEC = importlib.util.spec_from_file_location("smoke_installed_package", SMOKE_PATH)
if SMOKE_SPEC is None or SMOKE_SPEC.loader is None:
    raise RuntimeError(f"Unable to load smoke script from {SMOKE_PATH}")
smoke_installed_package = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(smoke_installed_package)


class SmokeInstalledPackageTests(unittest.TestCase):
    def _write_exported_skill(self, export_root: Path) -> None:
        skill_root = export_root / smoke_installed_package.SKILL_NAME
        for relative in smoke_installed_package.EXPECTED_RELATIVE_FILES:
            target = skill_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("ok\n", encoding="utf-8")

    def _write_archive(self, artifact: Path) -> None:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(artifact, "w") as archive:
            for relative in smoke_installed_package.EXPECTED_RELATIVE_FILES:
                archive.writestr(f"{smoke_installed_package.SKILL_NAME}/{relative}", "ok\n")

    def test_run_command_passes_timeout_and_returns_stdout(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["tool"],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        with mock.patch.object(smoke_installed_package.subprocess, "run", return_value=completed) as run:
            stdout = smoke_installed_package.run_command(["tool"])

        self.assertEqual(stdout, "ok")
        run.assert_called_once_with(
            ["tool"],
            capture_output=True,
            text=True,
            check=False,
            timeout=smoke_installed_package.command_timeout_seconds(),
        )

    def test_run_command_honors_timeout_env_override(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["tool"],
            returncode=0,
            stdout="ok\n",
            stderr="",
        )

        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "45"},
            clear=False,
        ):
            with mock.patch.object(
                smoke_installed_package.subprocess, "run", return_value=completed
            ) as run:
                smoke_installed_package.run_command(["tool"])

        self.assertEqual(run.call_args.kwargs["timeout"], 45)

    def test_run_command_rejects_non_integer_timeout(self) -> None:
        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "abc"},
            clear=False,
        ):
            with self.assertRaises(AssertionError) as context:
                smoke_installed_package.command_timeout_seconds()

        self.assertIn(smoke_installed_package.COMMAND_TIMEOUT_ENVVAR, str(context.exception))

    def test_run_command_rejects_non_positive_timeout(self) -> None:
        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "0"},
            clear=False,
        ):
            with self.assertRaises(AssertionError) as context:
                smoke_installed_package.command_timeout_seconds()

        self.assertIn(smoke_installed_package.COMMAND_TIMEOUT_ENVVAR, str(context.exception))

    def test_run_command_raises_assertion_on_non_zero_exit(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["tool"],
            returncode=2,
            stdout="partial stdout\n",
            stderr="partial stderr\n",
        )

        with mock.patch.object(smoke_installed_package.subprocess, "run", return_value=completed):
            with self.assertRaises(AssertionError) as context:
                smoke_installed_package.run_command(["tool"])

        message = str(context.exception)
        self.assertIn("Command failed (2)", message)
        self.assertIn("partial stdout", message)
        self.assertIn("partial stderr", message)

    def test_run_command_raises_assertion_on_timeout(self) -> None:
        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "45"},
            clear=False,
        ):
            timeout = subprocess.TimeoutExpired(
                cmd=["tool"],
                timeout=45,
                output="partial stdout",
                stderr="partial stderr",
            )

            with mock.patch.object(smoke_installed_package.subprocess, "run", side_effect=timeout):
                with self.assertRaises(AssertionError) as context:
                    smoke_installed_package.run_command(["tool"])

        message = str(context.exception)
        self.assertIn("timed out after 45s", message)
        self.assertIn("partial stdout", message)
        self.assertIn("partial stderr", message)

    def test_run_command_normalizes_missing_timeout_output(self) -> None:
        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "45"},
            clear=False,
        ):
            timeout = subprocess.TimeoutExpired(
                cmd=["tool"],
                timeout=45,
                output=None,
                stderr=None,
            )

            with mock.patch.object(smoke_installed_package.subprocess, "run", side_effect=timeout):
                with self.assertRaises(AssertionError) as context:
                    smoke_installed_package.run_command(["tool"])

        message = str(context.exception)
        self.assertIn("stdout:\n\nstderr:\n", message)

    def test_run_command_normalizes_bytes_timeout_output(self) -> None:
        with mock.patch.dict(
            smoke_installed_package.os.environ,
            {smoke_installed_package.COMMAND_TIMEOUT_ENVVAR: "45"},
            clear=False,
        ):
            timeout = subprocess.TimeoutExpired(
                cmd=["tool"],
                timeout=45,
                output=b"partial stdout",
                stderr=b"partial stderr",
            )

            with mock.patch.object(smoke_installed_package.subprocess, "run", side_effect=timeout):
                with self.assertRaises(AssertionError) as context:
                    smoke_installed_package.run_command(["tool"])

        message = str(context.exception)
        self.assertIn("partial stdout", message)
        self.assertIn("partial stderr", message)

    def test_assert_expected_skill_files_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_root = Path(tmp_dir) / smoke_installed_package.SKILL_NAME
            skill_root.mkdir()

            with self.assertRaises(AssertionError) as context:
                smoke_installed_package.assert_expected_skill_files(skill_root)

        self.assertIn("Expected file is missing", str(context.exception))

    def test_assert_expected_archive_files_rejects_missing_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact = Path(tmp_dir) / "artifact.zip"
            with zipfile.ZipFile(artifact, "w") as archive:
                archive.writestr(f"{smoke_installed_package.SKILL_NAME}/SKILL.md", "ok\n")

            with self.assertRaises(AssertionError) as context:
                smoke_installed_package.assert_expected_archive_files(artifact)

        self.assertIn("Expected archive member is missing", str(context.exception))

    def test_main_returns_zero_for_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_dir = Path(tmp_dir) / "bin"
            bin_dir.mkdir()
            fake_python = bin_dir / "python3"
            fake_python.write_text("", encoding="utf-8")
            console_script = bin_dir / smoke_installed_package.SKILL_NAME
            console_script.write_text("", encoding="utf-8")
            installed_version = "0.1.0"

            def option_value(args: list[str], option: str) -> str | None:
                if option not in args:
                    return None
                index = args.index(option)
                if index + 1 >= len(args):
                    return None
                return args[index + 1]

            def module_subcommand(args: list[str]) -> str | None:
                if not args or args[0] != str(fake_python):
                    return None
                try:
                    module_index = args.index("-m")
                except ValueError:
                    return None
                if module_index + 2 >= len(args):
                    return None
                if args[module_index + 1] != "scrutinize_me_skill":
                    return None
                return args[module_index + 2]

            def fake_run_command(args: list[str]) -> str:
                if module_subcommand(args) == "version":
                    return installed_version
                if args == [str(console_script), "version"]:
                    return installed_version
                if module_subcommand(args) == "export":
                    target_root = option_value(args, "--target-root")
                    if target_root is None:
                        raise AssertionError(f"Missing --target-root option: {args}")
                    export_root = Path(target_root)
                    self._write_exported_skill(export_root)
                    return str(export_root / smoke_installed_package.SKILL_NAME)
                if module_subcommand(args) == "build":
                    output_dir = option_value(args, "--output-dir")
                    if output_dir is None:
                        raise AssertionError(f"Missing --output-dir option: {args}")
                    build_root = Path(output_dir)
                    artifact = build_root / f"{smoke_installed_package.SKILL_NAME}-{installed_version}.zip"
                    self._write_archive(artifact)
                    return str(artifact)
                raise AssertionError(f"Unexpected command: {args}")

            with mock.patch.object(smoke_installed_package.sys, "executable", str(fake_python)):
                with mock.patch.object(
                    smoke_installed_package.importlib.metadata,
                    "version",
                    return_value=installed_version,
                ):
                    with mock.patch.object(
                        smoke_installed_package, "run_command", side_effect=fake_run_command
                    ):
                        self.assertEqual(smoke_installed_package.main(), 0)

    def test_main_rejects_missing_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_dir = Path(tmp_dir) / "bin"
            bin_dir.mkdir()
            fake_python = bin_dir / "python3"
            fake_python.write_text("", encoding="utf-8")

            with mock.patch.object(smoke_installed_package.sys, "executable", str(fake_python)):
                with mock.patch.object(
                    smoke_installed_package.importlib.metadata,
                    "version",
                    return_value="0.1.0",
                ):
                    with mock.patch.object(
                        smoke_installed_package,
                        "run_command",
                        return_value="0.1.0",
                    ):
                        with self.assertRaises(AssertionError) as context:
                            smoke_installed_package.main()

        self.assertIn("console script is missing", str(context.exception))

    def test_main_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            bin_dir = Path(tmp_dir) / "bin"
            bin_dir.mkdir()
            fake_python = bin_dir / "python3"
            fake_python.write_text("", encoding="utf-8")

            with mock.patch.object(smoke_installed_package.sys, "executable", str(fake_python)):
                with mock.patch.object(
                    smoke_installed_package.importlib.metadata,
                    "version",
                    return_value="0.1.0",
                ):
                    with mock.patch.object(
                        smoke_installed_package,
                        "run_command",
                        return_value="0.2.0",
                    ):
                        with self.assertRaises(AssertionError) as context:
                            smoke_installed_package.main()

        self.assertIn("Installed distribution version mismatch", str(context.exception))
