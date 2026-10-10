"""`rules` command — register and list business rules."""

from __future__ import annotations

import argparse

from ..core.result import CommandResult
from .base import add_common_arguments, build_container, run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="automation.rules", description="Business rule registry.")
    add_common_arguments(parser)
    sub = parser.add_subparsers(dest="action", required=True)

    register = sub.add_parser("register", help="Register a new business rule.")
    register.add_argument("--name", required=True, help="Rule name, e.g. '90 Day Average'.")
    register.add_argument("--module", required=True, help="Owning module, e.g. 'Procurement'.")
    register.add_argument("--priority", default="Medium",
                          choices=["Low", "Medium", "High", "Critical"])
    register.add_argument("--status", default="Active",
                          choices=["Draft", "Active", "Deprecated", "Retired"])
    register.add_argument("--version", default="1.0")
    register.add_argument("--depends", default="", help="Comma-separated dependency rule IDs.")
    register.add_argument("--description", default="", help="One-line description.")

    sub.add_parser("list", help="List registered business rules.")
    return parser


def _handle(args: argparse.Namespace) -> CommandResult:
    container = build_container(args)
    service = container.business_rule_service
    if args.action == "register":
        depends = [d.strip() for d in args.depends.split(",") if d.strip()]
        return service.register(
            name=args.name,
            module=args.module,
            priority=args.priority,
            status=args.status,
            version=args.version,
            dependencies=depends,
            description=args.description,
        )
    rules = service.list_rules()
    if not rules:
        return CommandResult.success("No business rules registered.")
    lines = [f"{r.identifier}  {r.name}  [{r.module}/{r.priority}/{r.status}]" for r in rules]
    return CommandResult.success(f"{len(rules)} rule(s):\n  " + "\n  ".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(_handle, args)
