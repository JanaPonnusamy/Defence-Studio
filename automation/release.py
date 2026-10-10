"""Entry point: ``python -m automation.release`` → release command handler."""

from __future__ import annotations

import sys

from .commands.release_cmd import main

if __name__ == "__main__":
    sys.exit(main())
