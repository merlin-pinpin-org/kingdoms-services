"""ChannelService: channel category management.

Resolution order: cache -> database -> platform creation.
Implemented in kingdoms-services#5.
"""

from __future__ import annotations

from kingdoms.core.interfaces.platform import IChannel, IPlatform


class ChannelService:
    """Manage channels by category; never by name or ID."""

    def __init__(self, platform: IPlatform) -> None:
        self._platform = platform

    async def get_channel_for_category(self, guild_id: str, category: str) -> IChannel:
        """Resolve a channel for a category (cache -> database -> platform creation)."""
        raise NotImplementedError("Implemented in kingdoms-services#5")
