"""Platform exceptions (kingdoms-services#10).

Failures of the platform frontends (Discord today, others per
ADR-0012). The taxonomy stays **core-agnostic**: no discord import
here, serializable context only (ADR-0019 rule 3).
"""

from __future__ import annotations

from kingdoms.core.exceptions.base import ErrorContext, KingdomsError, NotFoundError

__all__ = [
    "ChannelNotFoundError",
    "MessageSendError",
    "PlatformError",
    "PlatformNotSupportedError",
    "UserNotFoundError",
]


class PlatformError(KingdomsError):
    """Base error for platform failures."""

    code = "PLATFORM_ERROR"

    def __init__(self, message: str, platform: str = "") -> None:
        """Create the error, naming the platform."""
        context = ErrorContext().extend(platform=platform) if platform else ErrorContext()
        super().__init__(message, context)


class PlatformNotSupportedError(PlatformError):
    """Raised when a platform does not support a feature."""

    def __init__(self, platform: str, feature: str) -> None:
        """Create the error, naming the missing feature."""
        super().__init__(f"Platform '{platform}' does not support '{feature}'", platform=platform)
        self.context = self.context.extend(feature=feature)


class MessageSendError(PlatformError):
    """Raised when message sending fails."""

    def __init__(self, message: str, platform: str, channel_id: str = "") -> None:
        """Create the error, naming the channel."""
        super().__init__(message, platform=platform)
        if channel_id:
            self.context = self.context.extend(channel_id=channel_id)


class ChannelNotFoundError(NotFoundError):
    """Raised when a channel does not exist on the platform."""

    def __init__(self, channel_id: str, platform: str) -> None:
        """Create the error, naming the platform."""
        super().__init__("Channel", channel_id, f"Channel {channel_id} not found on {platform}")
        self.context = self.context.extend(platform=platform)


class UserNotFoundError(NotFoundError):
    """Raised when a user does not exist on the platform."""

    def __init__(self, user_id: str, platform: str) -> None:
        """Create the error, naming the platform."""
        super().__init__("User", user_id, f"User {user_id} not found on {platform}")
        self.context = self.context.extend(platform=platform)
