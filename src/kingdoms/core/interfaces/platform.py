"""Platform abstraction contracts (IPlatform, IMessage, IChannel, IUser, IWorkflow).

Structural typing via ``typing.Protocol`` (ADR-0011): implementations do not
inherit from these classes, they just satisfy the shape, checked by mypy.
``@runtime_checkable`` enables lightweight isinstance checks (member
presence only, not signatures).

Reference: docs/ARCHITECTURE.md (kingdoms repo) and kingdoms-services#3.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IUser(Protocol):
    """Platform-agnostic user identity."""

    @property
    def id(self) -> str:
        """Platform-specific unique user identifier."""
        ...

    @property
    def display_name(self) -> str:
        """Human-readable user name."""
        ...


@runtime_checkable
class IChannel(Protocol):
    """Platform-agnostic channel."""

    @property
    def id(self) -> str:
        """Platform-specific unique channel identifier."""
        ...

    @property
    def name(self) -> str:
        """Human-readable channel name."""
        ...


@runtime_checkable
class IMessage(Protocol):
    """Platform-agnostic message exchanged between the core and a platform."""

    @property
    def id(self) -> str:
        """Platform-specific unique message identifier."""
        ...

    @property
    def content(self) -> str:
        """Message text content."""
        ...

    @property
    def author(self) -> IUser:
        """Message author."""
        ...

    @property
    def channel(self) -> IChannel:
        """Channel the message was sent in."""
        ...


@runtime_checkable
class IPlatform(Protocol):
    """Platform abstraction: messaging, channel management, roles, DMs.

    First implementation: DiscordPlatform (kingdoms-services#11).
    """

    async def send_message(self, channel: IChannel, content: str) -> IMessage:
        """Send a text message to a channel."""
        ...

    async def send_dm(self, user: IUser, content: str) -> IMessage:
        """Send a direct message to a user."""
        ...

    async def create_channel(self, guild_id: str, category: str) -> IChannel:
        """Create a channel in a guild under a channel category."""
        ...

    async def assign_role(self, user: IUser, role_key: str) -> None:
        """Assign a role (referenced by logical role key) to a user."""
        ...


@runtime_checkable
class IWorkflow(Protocol):
    """Contract for an interaction sequence executed by the WorkflowEngine."""

    async def start(self, context: dict[str, Any]) -> None:
        """Start the workflow with an initial context."""
        ...

    async def handle_interaction(self, event: dict[str, Any]) -> None:
        """Handle a user interaction event and drive step transitions."""
        ...

    def steps(self) -> list[str]:
        """Return the ordered step names of the workflow."""
        ...
