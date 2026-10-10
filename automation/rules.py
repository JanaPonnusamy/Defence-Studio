"""Entry point: ``python -m automation.rules`` → business-rule command handler."""

from __future__ import annotations

import sys

from .commands.rules_cmd import main

if __name__ == "__main__":
    sys.exit(main())
