"""ChannelService: channel category management.

Resolves any category key — platform-level (ChannelCategory enum) or
mod-scoped (``mod:key``, declared in mod YAML and exposed by ModRegistry)
— to a concrete channel. Resolution order: cache -> database -> platform
creation. The service is generic: it never enumerates mod categories.

Implemented in kingdoms-services#5. Mod-scoped categories:
kingdoms-services#26, ADR-0003.
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

    async def setup_mod_channels(self, guild_id: str, mod_name: str) -> dict[str, IChannel]:
        """Provision every channel category declared by a mod.

        Generic: reads the mod's declaration via ModRegistry and resolves
        each ``mod:key`` category with the same flow as any other category.
        """
        raise NotImplementedError("Implemented in kingdoms-services#26")
