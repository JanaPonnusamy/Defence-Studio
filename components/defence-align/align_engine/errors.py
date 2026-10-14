"""Normalized error hierarchy with stable codes and HTTP status mapping.

The API layer renders any `AlignError` as `{"detail", "code"}` with the
carried `status_code`, so every rejection path has a consistent contract.
"""
from __future__ import annotations


class AlignError(Exception):
    status_code = 400
    code = "align_error"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class RequestValidationError(AlignError):
    status_code = 422
    code = "request_invalid"


class UnsafePathError(RequestValidationError):
    code = "unsafe_path"


class HashMismatchError(RequestValidationError):
    code = "hash_mismatch"


class MissingAudioError(RequestValidationError):
    code = "audio_missing"


class UnsupportedAudioFormatError(RequestValidationError):
    code = "audio_format_unsupported"


class UnsupportedLanguageError(RequestValidationError):
    code = "language_unsupported"


class UnsupportedCapabilityError(AlignError):
    status_code = 422
    code = "capability_unsupported"


class AlignmentOutputInvalidError(AlignError):
    """The engine produced structurally invalid timing (NaN/negative/out of
    order/beyond measured duration). Carries machine-readable issues so the
    caller sees exactly what failed rather than a trusted-looking artifact."""

    status_code = 422
    code = "alignment_output_invalid"

    def __init__(self, message: str, issues: list[dict] | None = None):
        super().__init__(message)
        self.issues = issues or []


class JobNotFoundError(AlignError):
    status_code = 404
    code = "job_not_found"


class JobConflictError(AlignError):
    status_code = 409
    code = "job_conflict"


class JobStateError(AlignError):
    status_code = 409
    code = "job_state_invalid"
