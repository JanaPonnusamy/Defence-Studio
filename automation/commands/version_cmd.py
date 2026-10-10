"""`version` command — bump/show/init the platform version in VERSION.md."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.version", description="Version management.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)

    bump = sub.add_parser("bump", help="Increment the version.")
    bump.add_argument("level", choices=["major", "minor", "patch", "build"])

    sub.add_parser("show", help="Show the current version.")
    sub.add_parser("init", help="Create VERSION.md if it does not exist.")

    rename = sub.add_parser("set-release-name", help="Set the release name.")
    rename.add_argument("name")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    service = container.version_service
    if args.action == "bump":
        return service.bump(args.level)
    if args.action == "init":
        return service.ensure_initialized()
    if args.action == "set-release-name":
        return service.set_release_name(args.name)
    # show
    version = service.current()
    return CommandResult.success(
        f"{version.semver} (build {version.build_number}) - {version.release_name} "
        f"[{version.branch or 'n/a'}@{version.git_commit or 'n/a'}]",
        data={"version": version.full},
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
