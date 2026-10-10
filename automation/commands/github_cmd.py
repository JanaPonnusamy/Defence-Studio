"""`github` command — detect, group, commit, push (optional tag)."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.github", description="GitHub synchronization.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    sync = sub.add_parser("sync", help="Commit grouped changes and push.")
    sync.add_argument("--tag", action="store_true", help="Create and push a version tag.")
    sync.add_argument("--no-push", action="store_true", help="Commit only; do not push.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    return container.github_sync_service.sync(create_tag=args.tag, push=not args.no_push)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
