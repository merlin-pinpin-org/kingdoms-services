"""DiscordPlatform: IPlatform implementation on top of discord.py.

Implemented in kingdoms-services#11.
"""

from __future__ import annotations

from kingdoms.core.interfaces.platform import IChannel, IMessage, IPlatform, IUser


class DiscordPlatform(IPlatform):
    """Discord implementation of IPlatform."""

    async def send_message(self, channel: IChannel, content: str) -> IMessage:
        """Send a text message to a Discord channel."""
        raise NotImplementedError("Implemented in kingdoms-services#11")

    async def send_dm(self, user: IUser, content: str) -> IMessage:
        """Send a direct message to a Discord user."""
        raise NotImplementedError("Implemented in kingdoms-services#11")

    async def create_channel(self, guild_id: str, category: str) -> IChannel:
        """Create a Discord channel under a channel category."""
        raise NotImplementedError("Implemented in kingdoms-services#11")

    async def assign_role(self, user: IUser, role_key: str) -> None:
        """Assign a Discord role referenced by logical role key."""
        raise NotImplementedError("Implemented in kingdoms-services#11")
