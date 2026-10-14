"""Machine-readable error model for the Audio Module API."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AudioModuleError(Exception):
    """Base error with a stable machine-readable error_code."""

    status_code = 500
    error_code = "internal_error"

    def __init__(self, message: str, *, error_code: str | None = None, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AudioModuleError):
    status_code = 404
    error_code = "not_found"


class ValidationFailedError(AudioModuleError):
    status_code = 422
    error_code = "validation_failed"


class UnsupportedMediaError(AudioModuleError):
    status_code = 415
    error_code = "unsupported_media"


class RightsMetadataRequiredError(AudioModuleError):
    status_code = 403
    error_code = "rights_metadata_required"


class JobFailedError(AudioModuleError):
    status_code = 409
    error_code = "job_failed"


async def audio_module_error_handler(request: Request, exc: AudioModuleError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )
