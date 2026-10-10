"""Configuration-driven logger factory for NDF.

Services and providers obtain loggers through :func:`get_logger`; logging level and
format come from configuration. Application logic never uses ``print`` — command
handlers are responsible for user-facing output.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False
_ROOT_NAME = "ndf"
_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def configure_logging(level: str = "INFO", fmt: str | None = None) -> None:
    """Configure the NDF logger hierarchy once."""
    global _CONFIGURED
    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(fmt or _DEFAULT_FORMAT))
        logger.addHandler(handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced child logger (e.g. ``ndf.services.version``)."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
