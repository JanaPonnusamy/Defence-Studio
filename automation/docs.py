"""Entry point: ``python -m automation.docs`` → docs command handler."""

from __future__ import annotations

import sys

from .commands.docs_cmd import main

if __name__ == "__main__":
    sys.exit(main())
