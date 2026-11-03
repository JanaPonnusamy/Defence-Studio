"""Masking of employer identifiers and redaction of anything secret-shaped in logs."""
from __future__ import annotations

import logging
import re

# ESIC employer codes are 17 digits. Insurance numbers are 10 digits.
_EMPLOYER_CODE = re.compile(r"(?<!\d)(\d{2})\d{12}(\d{3})(?!\d)")
_SECRET_KV = re.compile(r"(?i)(password|passwd|pwd|otp|captcha|txtpassword|txtchallancaptcha)(\s*[=:]\s*)([^&\s,;]+)")


def mask_employer_code(code: str | None) -> str | None:
    if not code:
        return None
    digits = re.sub(r"\D", "", code)
    if len(digits) < 6:
        return "*" * len(digits)
    return digits[:2] + "*" * (len(digits) - 5) + digits[-3:]


def redact(text: str) -> str:
    text = _EMPLOYER_CODE.sub(lambda m: m.group(1) + "*" * 12 + m.group(2), text)
    return _SECRET_KV.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        record.msg, record.args = redact(message), ()
        return True


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("esic_audit")
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
