"""`health` command — generate the repository health report."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.health", description="Repository health.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("report", help="Generate docs/REPORTS/repo_health.md.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    return container.repo_health_service.report()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
