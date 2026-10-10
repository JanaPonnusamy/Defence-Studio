"""Entry point: ``python -m automation.version`` → version command handler."""

from __future__ import annotations

import sys

from .commands.version_cmd import main

if __name__ == "__main__":
    sys.exit(main())
