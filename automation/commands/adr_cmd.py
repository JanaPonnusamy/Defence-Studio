"""`adr` command — create and list Architecture Decision Records."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.adr", description="Architecture Decision Records.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)

    new = sub.add_parser("new", help="Create the next-numbered ADR.")
    new.add_argument("title", help="ADR title, e.g. 'Business Cycle'.")
    new.add_argument("--status", default="Proposed",
                    choices=["Proposed", "Accepted", "Superseded", "Deprecated", "Rejected"])
    new.add_argument("--deciders", default="", help="Comma-separated deciders.")

    sub.add_parser("list", help="List ADRs.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    service = container.adr_service
    if args.action == "new":
        return service.create(args.title, status=args.status, deciders=args.deciders)
    records = service.list_records()
    if not records:
        return CommandResult.success("No ADRs recorded.")
    lines = [f"{r.identifier}  {r.title}  [{r.status}]" for r in records]
    return CommandResult.success(f"{len(records)} ADR(s):\n  " + "\n  ".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
