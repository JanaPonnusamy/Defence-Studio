"""`docs` command — build the documentation index (and ADR/rule indexes)."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.docs", description="Documentation registry.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("build", help="Scan docs and rebuild docs/INDEX.md.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    return container.docs_registry_service.build()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
