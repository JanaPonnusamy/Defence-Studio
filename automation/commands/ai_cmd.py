"""`ai` command — manage the AI documentation archive."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.ai", description="AI documentation archive.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)

    add = sub.add_parser("add", help="Archive an AI-generated artifact.")
    add.add_argument("--source", required=True, help="AI source, e.g. ChatGPT or Claude.")
    add.add_argument("--title", required=True)
    add.add_argument("--kind", default="output", choices=["prompt", "output"])
    add.add_argument("--body", default="", help="Optional artifact body text.")

    approve = sub.add_parser("approve", help="Approve an archived artifact.")
    approve.add_argument("identifier")

    reject = sub.add_parser("reject", help="Reject an archived artifact.")
    reject.add_argument("identifier")

    sub.add_parser("list", help="List archived AI artifacts.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    service = container.ai_archive_service
    if args.action == "add":
        return service.add(args.source, args.title, kind=args.kind, body=args.body)
    if args.action == "approve":
        return service.set_status(args.identifier, "Approved")
    if args.action == "reject":
        return service.set_status(args.identifier, "Rejected")
    artifacts = service.list_artifacts()
    if not artifacts:
        return CommandResult.success("No AI artifacts archived.")
    lines = [f"{a.identifier}  {a.title}  [{a.source}/{a.approval_status} r{a.revision}]"
             for a in artifacts]
    return CommandResult.success(f"{len(artifacts)} artifact(s):\n  " + "\n  ".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
