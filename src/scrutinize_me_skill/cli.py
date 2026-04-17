from __future__ import annotations

import argparse
from pathlib import Path

from scrutinize_me_skill import __version__
from scrutinize_me_skill.builder import build_release_zip, materialize_skill


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrutinize-me",
        description="Manage the Scrutinize Me agent skill from its src-backed source of truth.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_cmd = subparsers.add_parser("build", help="Create a versioned release zip.")
    build_cmd.add_argument("--output-dir", type=Path, default=Path("dist"))
    build_cmd.add_argument("--version", default=__version__)
    build_cmd.add_argument("--release-tag")

    export_cmd = subparsers.add_parser("export", help="Copy the skill into a discoverable skill directory.")
    export_cmd.add_argument("--target-root", type=Path, default=Path(".agents/skills"))

    subparsers.add_parser("version", help="Print the package version.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        artifact = build_release_zip(
            output_dir=args.output_dir,
            version=args.version,
            release_tag=args.release_tag,
        )
        print(artifact)
        return 0

    if args.command == "export":
        destination = materialize_skill(args.target_root)
        print(destination)
        return 0

    if args.command == "version":
        print(__version__)
        return 0

    parser.error(f"Unsupported command: {args.command}")
    return 2
