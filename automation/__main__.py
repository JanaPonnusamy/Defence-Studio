"""Unified dispatcher: ``python -m automation <group> <action> [options]``.

Groups map to the same command handlers used by the individual entry points
(``python -m automation.version`` etc.), so both invocation styles are equivalent.
"""

from __future__ import annotations

import sys

from . import FRAMEWORK_NAME, __version__

_GROUPS = {
    "version": "automation.commands.version_cmd",
    "docs": "automation.commands.docs_cmd",
    "release": "automation.commands.release_cmd",
    "github": "automation.commands.github_cmd",
    "dashboard": "automation.commands.dashboard_cmd",
    "rules": "automation.commands.rules_cmd",
    "adr": "automation.commands.adr_cmd",
    "ai": "automation.commands.ai_cmd",
    "health": "automation.commands.health_cmd",
}


def _usage() -> str:
    groups = ", ".join(sorted(_GROUPS))
    return (
        f"{FRAMEWORK_NAME} (NDF) v{__version__}\n"
        f"Usage: python -m automation <group> <action> [options]\n"
        f"Groups: {groups}\n"
        f"Example: python -m automation version bump minor"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    if argv[0] in ("-v", "--version"):
        print(f"{FRAMEWORK_NAME} v{__version__}")
        return 0

    group = argv[0]
    if group not in _GROUPS:
        print(f"Unknown group '{group}'.\n\n{_usage()}", file=sys.stderr)
        return 2

    import importlib

    module = importlib.import_module(_GROUPS[group])
    return module.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
