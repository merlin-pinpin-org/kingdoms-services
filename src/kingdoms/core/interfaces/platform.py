"""Platform abstraction contracts (IPlatform, IMessage, IChannel, IUser, IWorkflow).

Reference: docs/ARCHITECTURE.md (kingdoms repo) and kingdoms-services#3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class IUser(ABC):
    """Platform-agnostic user identity."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Platform-specific unique user identifier."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable user name."""


class IChannel(ABC):
    """Platform-agnostic channel."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Platform-specific unique channel identifier."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable channel name."""


class IMessage(ABC):
    """Platform-agnostic message exchanged between the core and a platform."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Platform-specific unique message identifier."""

    @property
    @abstractmethod
    def content(self) -> str:
        """Message text content."""

    @property
    @abstractmethod
    def author(self) -> IUser:
        """Message author."""

    @property
    @abstractmethod
    def channel(self) -> IChannel:
        """Channel the message was sent in."""


class IPlatform(ABC):
    """Platform abstraction: messaging, channel management, roles, DMs.

    First implementation: DiscordPlatform (kingdoms-services#11).
    """

    @abstractmethod
    async def send_message(self, channel: IChannel, content: str) -> IMessage:
        """Send a text message to a channel."""

    @abstractmethod
    async def send_dm(self, user: IUser, content: str) -> IMessage:
        """Send a direct message to a user."""

    @abstractmethod
    async def create_channel(self, guild_id: str, category: str) -> IChannel:
        """Create a channel in a guild under a channel category."""

    @abstractmethod
    async def assign_role(self, user: IUser, role_key: str) -> None:
        """Assign a role (referenced by logical role key) to a user."""


class IWorkflow(ABC):
    """Contract for an interaction sequence executed by the WorkflowEngine."""

    @abstractmethod
    async def start(self, context: dict[str, Any]) -> None:
        """Start the workflow with an initial context."""

    @abstractmethod
    async def handle_interaction(self, event: dict[str, Any]) -> None:
        """Handle a user interaction event and drive step transitions."""

    @abstractmethod
    def steps(self) -> list[str]:
        """Return the ordered step names of the workflow."""
