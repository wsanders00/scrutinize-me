from pathlib import Path
import sys
import types
import unittest


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from scrutinize_me_skill.builder_platform import ensure_supported_platform


class PlatformSupportTests(unittest.TestCase):
    def test_ensure_supported_platform_accepts_required_capabilities(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        ensure_supported_platform(
            os_module=fake_os,
            fcntl_module=object(),
            directory_flag=1,
            nofollow_flag=1,
        )

    def test_ensure_supported_platform_rejects_non_posix(self) -> None:
        fake_os = types.SimpleNamespace(name="nt", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("os.name='nt'", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_fcntl(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=None,
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("fcntl.flock", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_fwalk(self) -> None:
        fake_os = types.SimpleNamespace(name="posix")
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=1,
            )

        self.assertIn("os.fwalk", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_directory_flag(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=0,
                nofollow_flag=1,
            )

        self.assertIn("os.O_DIRECTORY", str(context.exception))

    def test_ensure_supported_platform_rejects_missing_nofollow_flag(self) -> None:
        fake_os = types.SimpleNamespace(name="posix", fwalk=lambda *args, **kwargs: None)
        with self.assertRaises(RuntimeError) as context:
            ensure_supported_platform(
                os_module=fake_os,
                fcntl_module=object(),
                directory_flag=1,
                nofollow_flag=0,
            )

        self.assertIn("os.O_NOFOLLOW", str(context.exception))


if __name__ == "__main__":
    unittest.main()
