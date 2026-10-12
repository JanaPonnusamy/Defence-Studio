"""
Explicit failure codes (spec §31) and the module-wide error envelope.

Failures must be explicit -- never silently replace factual source material
with fabricated AI imagery, and never fabricate a SUCCEEDED job result.

Error envelope returned by every API error:
    {"error_code": "<CODE>", "error_message": "<human text>", "request_id": "<id>"}
Stack traces are logged server-side only and never returned to clients.
"""
from __future__ import annotations

import uuid

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

FAILURE_CODES = {
    # spec §31
    "SOURCE_UNAVAILABLE",
    "RIGHTS_UNKNOWN",
    "ASSET_INVALID",
    "GENERATION_FAILED",
    "PROVIDER_TIMEOUT",
    "PROVIDER_QUOTA",
    "MAP_RENDER_FAILED",
    "INVALID_STAT_DATA",
    "MISSING_NARRATION_ANCHOR",
    # Phase 2: provider states / timing
    "PROVIDER_NOT_CONFIGURED",
    "PROVIDER_UNAVAILABLE",
    "PROVIDER_AUTH_FAILED",
    "PROVIDER_RATE_LIMITED",
    "PROVIDER_RESPONSE_INVALID",
    "PROVIDER_CONTENT_REJECTED",
    "INVALID_TIMING",
    "PREVIEW_UNAVAILABLE",
    # module operational codes
    "NOT_FOUND",
    "VALIDATION_ERROR",
    "PAYLOAD_TOO_LARGE",
    "BUDGET_EXCEEDED",
    "PATH_TRAVERSAL_REJECTED",
    "INVALID_URL",
    "EDITORIAL_POLICY",
    "CONFLICT",
    "UNAUTHORIZED",
    "JOB_INTERRUPTED",
    "JOB_CANCELLED",
    "OUTPUT_VERIFICATION_FAILED",
    "INTERNAL_ERROR",
}

_DEFAULT_STATUS = {
    "NOT_FOUND": 404,
    "VALIDATION_ERROR": 422,
    "ASSET_INVALID": 422,
    "INVALID_URL": 422,
    "INVALID_STAT_DATA": 422,
    "MAP_RENDER_FAILED": 422,
    "MISSING_NARRATION_ANCHOR": 422,
    "INVALID_TIMING": 422,
    "PREVIEW_UNAVAILABLE": 409,
    "PROVIDER_NOT_CONFIGURED": 409,
    "PROVIDER_UNAVAILABLE": 503,
    "PROVIDER_AUTH_FAILED": 502,
    "PROVIDER_RATE_LIMITED": 503,
    "PROVIDER_RESPONSE_INVALID": 502,
    "PROVIDER_CONTENT_REJECTED": 422,
    "PROVIDER_TIMEOUT": 504,
    "PROVIDER_QUOTA": 502,
    "GENERATION_FAILED": 502,
    "RIGHTS_UNKNOWN": 422,
    "EDITORIAL_POLICY": 422,
    "PATH_TRAVERSAL_REJECTED": 400,
    "PAYLOAD_TOO_LARGE": 413,
    "BUDGET_EXCEEDED": 409,
    "CONFLICT": 409,
    "UNAUTHORIZED": 401,
    "INTERNAL_ERROR": 500,
}


class VideoModuleError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None):
        if code not in FAILURE_CODES:  # programming error -> still explicit, never silent
            message = f"[unregistered code {code}] {message}"
            code = "INTERNAL_ERROR"
        self.code = code
        self.message = message
        self.status_code = status_code or _DEFAULT_STATUS.get(code, 400)
        super().__init__(message)


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", None) or uuid.uuid4().hex[:12]


def error_body(code: str, message: str, request_id: str, details: list | None = None) -> dict:
    body = {"error_code": code, "error_message": message, "request_id": request_id}
    if details:
        body["details"] = details
    return body


async def video_module_error_handler(request: Request, exc: VideoModuleError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.message, _rid(request)))


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only field locations + messages; never echo submitted input values back.
    details = [
        {"loc": [str(p) for p in e.get("loc", [])], "msg": str(e.get("msg", ""))[:200]}
        for e in exc.errors()
    ][:20]
    return JSONResponse(
        status_code=422,
        content=error_body("VALIDATION_ERROR", "Request validation failed.", _rid(request), details),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "NOT_FOUND" if exc.status_code == 404 else ("UNAUTHORIZED" if exc.status_code == 401 else "VALIDATION_ERROR")
    msg = str(exc.detail) if exc.status_code != 500 else "An internal error occurred."
    return JSONResponse(status_code=exc.status_code, content=error_body(code, msg, _rid(request)))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    from app.logging_conf import get_logger

    rid = _rid(request)
    get_logger("errors").exception("Unhandled error request_id=%s path=%s", rid, request.url.path)
    return JSONResponse(
        status_code=500,
        content=error_body("INTERNAL_ERROR", "An internal error occurred. See server logs with this request_id.", rid),
    )
