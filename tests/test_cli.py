from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill import __version__
from scrutinize_me_skill import cli


class CliTest(TestCase):
    def test_version_command_prints_package_version_and_returns_zero(self):
        buffer = io.StringIO()

        with redirect_stdout(buffer):
            result = cli.main(["version"])

        self.assertEqual(result, 0)
        self.assertEqual(buffer.getvalue().strip(), __version__)

    @patch("scrutinize_me_skill.cli.materialize_skill")
    def test_export_command_logs_destination_and_returns_zero(self, mock_materialize):
        destination = Path("/tmp/scrutinize-me-export")
        mock_materialize.return_value = destination

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = cli.main(["export"])

        self.assertEqual(result, 0)
        self.assertEqual(buffer.getvalue().strip(), str(destination))
        mock_materialize.assert_called_once_with(Path(".agents/skills"), force=False)

    @patch("scrutinize_me_skill.cli.materialize_skill")
    def test_export_command_respects_flags(self, mock_materialize):
        destination = Path("/tmp/custom-export")
        mock_materialize.return_value = destination

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = cli.main(["export", "--force", "--target-root", "/tmp/skills"])

        self.assertEqual(result, 0)
        self.assertEqual(buffer.getvalue().strip(), str(destination))
        mock_materialize.assert_called_once_with(Path("/tmp/skills"), force=True)

    @patch("scrutinize_me_skill.cli.build_release_zip")
    def test_build_command_logs_artifact_and_returns_zero(self, mock_build_release_zip):
        artifact = Path("/tmp/scrutinize-me-0.1.0.zip")
        mock_build_release_zip.return_value = artifact

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = cli.main(["build"])

        self.assertEqual(result, 0)
        self.assertEqual(buffer.getvalue().strip(), str(artifact))
        mock_build_release_zip.assert_called_once_with(
            output_dir=Path("dist"),
            version=__version__,
            release_tag=None,
        )

    @patch("scrutinize_me_skill.cli.build_release_zip")
    def test_build_command_respects_explicit_flags(self, mock_build_release_zip):
        artifact = Path("/tmp/custom.zip")
        mock_build_release_zip.return_value = artifact

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = cli.main(
                [
                    "build",
                    "--output-dir",
                    "/tmp/dist",
                    "--version",
                    __version__,
                    "--release-tag",
                    f"v{__version__}",
                ]
            )

        self.assertEqual(result, 0)
        self.assertEqual(buffer.getvalue().strip(), str(artifact))
        mock_build_release_zip.assert_called_once_with(
            output_dir=Path("/tmp/dist"),
            version=__version__,
            release_tag=f"v{__version__}",
        )

    def test_invalid_invocation_raises_system_exit(self):
        stderr = io.StringIO()

        with self.assertRaises(SystemExit) as cm:
            with redirect_stderr(stderr):
                cli.main([])

        self.assertNotEqual(cm.exception.code, 0)
