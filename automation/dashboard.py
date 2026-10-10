"""Entry point: ``python -m automation.dashboard`` → dashboard command handler."""

from __future__ import annotations

import sys

from .commands.dashboard_cmd import main

if __name__ == "__main__":
    sys.exit(main())
