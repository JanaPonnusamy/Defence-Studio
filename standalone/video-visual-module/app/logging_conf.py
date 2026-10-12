"""Module-owned logging: stdout + rotating file under storage/logs/.

A redaction filter scrubs anything that looks like a credential (API keys,
bearer tokens, password=...) from every record before it is written, so
credentials never reach the log file or the diagnostics endpoint.
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

_CONFIGURED = False
LOG_FILE_NAME = "video_module.log"

_REDACT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|x-api-key|authorization|password|secret|token)(\s*[=:]\s*|\"\s*:\s*\")([^\s,\"';&]+)"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
]


def redact(text: str) -> str:
    text = _REDACT_PATTERNS[1].sub("Bearer [REDACTED]", text)
    text = _REDACT_PATTERNS[2].sub("[REDACTED]", text)
    text = _REDACT_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    from app.config import get_settings

    settings = get_settings()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger("video_module")
    root.setLevel(settings.log_level)
    root.propagate = False
    flt = RedactingFilter()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.addFilter(flt)
    root.addHandler(sh)

    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(settings.logs_dir / LOG_FILE_NAME, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(flt)
        root.addHandler(fh)
    except OSError:
        root.warning("File logging unavailable; logging to stdout only.")
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"video_module.{name}")


def tail_log(lines: int = 200) -> list[str]:
    from app.config import get_settings

    path = get_settings().logs_dir / LOG_FILE_NAME
    if not path.exists():
        return []
    lines = max(1, min(2000, lines))
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        content = fh.readlines()[-lines:]
    return [redact(line.rstrip("\n")) for line in content]
