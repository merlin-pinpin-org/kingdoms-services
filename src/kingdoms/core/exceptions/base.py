"""Base exception classes for Kingdoms (kingdoms-services#10).

Every custom exception of the platform inherits from
:class:`KingdomsError`. The hierarchy is **typed taxonomy**, not
string switching: the class is the category, the ``code`` rides the
logs and the audits, the ``user_key`` selects the user-facing i18n
message rendered by the handler.

ADR-0019 rule 3: these exceptions cross the service seams — they
carry **serializable data only** (strings, numbers), never platform
objects, so an extracted service can raise them across a boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ConfigurationError",
    "KingdomsError",
    "NotFoundError",
    "PermissionError",
    "RateLimitError",
    "ValidationError",
]

logger = logging.getLogger("kingdoms.core.exceptions")

USER_KEY_BY_CODE: dict[str, str] = {
    "VALIDATION_ERROR": "errors.validation",
    "CONFIGURATION_ERROR": "errors.configuration",
    "NOT_FOUND": "errors.not_found",
    "PERMISSION_ERROR": "errors.permission",
    "RATE_LIMIT_ERROR": "errors.rate_limited",
    "WORKFLOW_ERROR": "errors.workflow",
    "PLATFORM_ERROR": "errors.platform",
    "DATABASE_ERROR": "errors.database",
    "KINGDOMS_ERROR": "errors.unexpected",
}


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Serializable details riding the exception (ADR-0019 rule 3)."""

    data: dict[str, Any] = field(default_factory=dict)

    def extend(self, **kwargs: Any) -> ErrorContext:
        """Return a copy with the extra entries appended."""
        return ErrorContext(data={**self.data, **kwargs})


class KingdomsError(Exception):
    """Base error for all Kingdoms failures.

    ``code`` drives the logs and audits (machine-stable); ``user_key``
    selects the ephemeral i18n answer rendered to the user; ``context``
    carries serializable details (IDs, keys) — never platform objects.
    """

    code = "KINGDOMS_ERROR"

    def __init__(self, message: str, context: ErrorContext | None = None) -> None:
        """Create the error with its message and serializable context."""
        super().__init__(message)
        self.message = message
        self.context = context or ErrorContext()

    @property
    def user_key(self) -> str:
        """The i18n key of the user-facing answer."""
        return USER_KEY_BY_CODE.get(self.code, USER_KEY_BY_CODE["KINGDOMS_ERROR"])

    def to_dict(self) -> dict[str, Any]:
        """Serialize for audits and API responses."""
        return {"error": self.code, "message": self.message, "details": self.context.data}

    def __str__(self) -> str:
        """Render the log-friendly single-line shape: code and message."""
        return f"[{self.code}] {self.message}"


class ValidationError(KingdomsError):
    """Raised when input validation fails."""

    code = "VALIDATION_ERROR"

    def __init__(self, message: str, field: str = "", value: Any = None) -> None:
        """Create the error, carrying the offending field and value."""
        context = ErrorContext().extend(field=field) if field else ErrorContext()
        if value is not None:
            context = context.extend(value=str(value))
        super().__init__(message, context)


class ConfigurationError(KingdomsError):
    """Raised when configuration is invalid or missing."""

    code = "CONFIGURATION_ERROR"

    def __init__(self, message: str, config_key: str = "") -> None:
        """Create the error, carrying the offending config key."""
        context = ErrorContext().extend(config_key=config_key) if config_key else ErrorContext()
        super().__init__(message, context)


class NotFoundError(KingdomsError):
    """Raised when a resource does not exist."""

    code = "NOT_FOUND"

    def __init__(self, resource_type: str, resource_id: Any, message: str = "") -> None:
        """Create the error for one missing resource."""
        super().__init__(
            message or f"{resource_type} not found: {resource_id}",
            ErrorContext().extend(resource_type=resource_type, resource_id=str(resource_id)),
        )


class PermissionError(KingdomsError):
    """Raised when a user lacks a required permission.

    Deliberately shadows :class:`builtins.PermissionError` inside the
    package namespace: the base ``PermissionError`` means an OS-level
    file fault, never an authorization decision.
    """

    code = "PERMISSION_ERROR"

    def __init__(self, message: str, required_permission: str = "", user_id: str = "") -> None:
        """Create the error, carrying the denied permission and user."""
        context = ErrorContext()
        if required_permission:
            context = context.extend(required_permission=required_permission)
        if user_id:
            context = context.extend(user_id=user_id)
        super().__init__(message, context)


class RateLimitError(KingdomsError):
    """Raised when a rate limit is exceeded."""

    code = "RATE_LIMIT_ERROR"

    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 0, limit: int = 0) -> None:
        """Create the error, carrying the retry hint and the limit."""
        context = ErrorContext()
        if retry_after:
            context = context.extend(retry_after=retry_after)
        if limit:
            context = context.extend(limit=limit)
        super().__init__(message, context)
