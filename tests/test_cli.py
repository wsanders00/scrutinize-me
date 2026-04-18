from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

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

    def test_invalid_invocation_raises_system_exit(self):
        buffer = io.StringIO()

        with self.assertRaises(SystemExit) as cm:
            with redirect_stdout(buffer):
                cli.main([])

        self.assertNotEqual(cm.exception.code, 0)
