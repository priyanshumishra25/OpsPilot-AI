"""Domain error types. HTTP mapping lives in api/errors.py, not here."""

from __future__ import annotations

from typing import Any


class OpsPilotError(Exception):
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(OpsPilotError):
    code = "not_found"


class AuthenticationError(OpsPilotError):
    code = "unauthenticated"


class PermissionDeniedError(OpsPilotError):
    code = "permission_denied"


class ValidationFailedError(OpsPilotError):
    code = "validation_failed"


class ConflictError(OpsPilotError):
    code = "conflict"


class RateLimitedError(OpsPilotError):
    code = "rate_limited"


class ExtractionError(OpsPilotError):
    """Document content could not be extracted. Not retryable."""

    code = "extraction_failed"


class UpstreamUnavailableError(OpsPilotError):
    """A dependency (model API, object store) failed transiently. Retryable."""

    code = "upstream_unavailable"


class NotConfiguredError(OpsPilotError):
    code = "not_configured"
