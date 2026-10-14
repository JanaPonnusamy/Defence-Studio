"""Typed errors. Every failure carries a stable machine code and a human message."""
from __future__ import annotations

HTTP_STATUS = {
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INVALID_PACKAGE": 422,
    "INVALID_REQUEST": 422,
    "DURATION_MISMATCH": 422,
    "FFMPEG_NOT_AVAILABLE": 503,
}


class RendererError(Exception):
    def __init__(self, code: str, message: str, *, details: list[str] | None = None, status_code: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or []
        self.status_code = status_code or HTTP_STATUS.get(code, 400)


class Cancelled(Exception):
    """Cooperative cancellation requested for the running job."""


class PackageInvalid(RendererError):
    def __init__(self, problems: list[str]):
        head = problems[0] if problems else "invalid package"
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        super().__init__("INVALID_PACKAGE", f"{head}{more}", details=problems)
