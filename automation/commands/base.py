"""Shared command infrastructure: container bootstrap, result rendering, error handling."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..core.container import Container
from ..core.exceptions import NDFError
from ..core.result import CommandResult


def build_container(args: argparse.Namespace) -> Container:
    return Container(log_level=getattr(args, "log_level", None))


def render(result: CommandResult) -> int:
    """Print a CommandResult to stdout and return its exit code."""
    prefix = "OK" if result.ok else "ERROR"
    print(f"[{prefix}] {result.message}")
    for path in result.changed_paths:
        print(f"   - {path}")
    return result.exit_code


def run(handler: Callable[[argparse.Namespace], CommandResult], args: argparse.Namespace) -> int:
    """Execute a handler, converting NDFError into a clean failure result."""
    try:
        return render(handler(args))
    except NDFError as exc:
        return render(CommandResult.failure(str(exc)))


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override the configured log level.",
    )
