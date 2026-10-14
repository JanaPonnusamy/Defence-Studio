"""Logging: console plus an optional rotating file under storage/logs. No secrets or file contents are logged."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_configured = False


def configure(log_dir: Path | None = None) -> None:
    global _configured
    root = logging.getLogger("renderer")
    if _configured:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if log_dir is not None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(log_dir / "renderer.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"renderer.{name}")
