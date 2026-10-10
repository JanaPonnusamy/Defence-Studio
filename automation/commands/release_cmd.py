"""`release` command — generate CHANGELOG.md + RELEASE_NOTES.md (optional tag)."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.release", description="Release management.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build", help="Generate changelog + release notes for the current version.")
    build.add_argument("--tag", action="store_true", help="Also create a git tag for the release.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    return container.release_service.build(create_tag=args.tag)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
