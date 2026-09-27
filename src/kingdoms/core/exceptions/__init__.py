"""Kingdoms exception taxonomy (kingdoms-services#10).

One import for every failure type of the platform. The hierarchy is
typed (the class is the category), the ``code`` rides logs/audits,
the ``user_key`` selects the i18n answer, and the context carries
serializable data only (ADR-0019 rule 3 — no platform objects across
seams).
"""

from kingdoms.core.exceptions.base import (
    ConfigurationError,
    ErrorContext,
    KingdomsError,
    NotFoundError,
    PermissionError,
    RateLimitError,
    ValidationError,
)
from kingdoms.core.exceptions.database import (
    ConnectionError,
    DatabaseError,
    DuplicateKeyError,
    QueryError,
)
from kingdoms.core.exceptions.handlers import ExceptionHandler
from kingdoms.core.exceptions.platform import (
    ChannelNotFoundError,
    MessageSendError,
    PlatformError,
    PlatformNotSupportedError,
    UserNotFoundError,
)
from kingdoms.core.exceptions.workflow import (
    InvalidWorkflowStepError,
    WorkflowAlreadyCompletedError,
    WorkflowError,
    WorkflowNotFoundError,
    WorkflowTimeoutError,
)

__all__ = [
    "ChannelNotFoundError",
    "ConfigurationError",
    "ConnectionError",
    "DatabaseError",
    "DuplicateKeyError",
    "ErrorContext",
    "ExceptionHandler",
    "InvalidWorkflowStepError",
    "KingdomsError",
    "MessageSendError",
    "NotFoundError",
    "PermissionError",
    "PlatformError",
    "PlatformNotSupportedError",
    "QueryError",
    "RateLimitError",
    "UserNotFoundError",
    "ValidationError",
    "WorkflowAlreadyCompletedError",
    "WorkflowError",
    "WorkflowNotFoundError",
    "WorkflowTimeoutError",
]
