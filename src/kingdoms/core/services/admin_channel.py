"""Core admin channel service: the 🛡-bot-admins home (kingdoms-services#115).

The admin messages with actions (the enrollment screen, the admin
panels) live in a dedicated channel, so the admin surface is visible
and auditable — never scattered in gameplay channels. The service
follows the LogService provisioning pattern (cache-aside: Redis →
MongoDB → adoption → creation), plus the **transparency rule**
(developer-mandated):

- the channel is visible to the ``bot-admins`` role only (plus the
  guild administrators' inherent rights and the bot itself);
- the BOT_ADMINS are synced **into** the role — being an operator is
  public knowledge, and the role is the visible roster;
- their rights never depend on it: the runtime guards check
  ``BOT_ADMINS`` **first** (environment-sourced, impossible to strip
  from Discord), so removing the role costs the visibility, not the
  privileges.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.roles import RolesService

logger = logging.getLogger("kingdoms.core.admin_channel")

ADMIN_CHANNEL_CATEGORY = "bot_admins"
ADMIN_CHANNEL_NAME = "🛡-bot-admins"
ADMIN_CHANNELS_COLLECTION = "channels"


class AdminChannelPlatform(Protocol):
    """Narrow platform seam: find, create, sync the admin channel."""

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild channel id by its exact name; None when absent."""
        ...

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        """Create the channel; return its id."""
        ...

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        ...

    async def sync_admin_role_members(
        self,
        guild_id: str,
        role_id: str,
        admin_ids: tuple[str, ...],
    ) -> None:
        """Ensure every BOT_ADMIN holds the role (add, never remove)."""
        ...

    async def apply_channel_policy(self, guild_id: str, channel_id: str, role_id: str) -> None:
        """Admin-channel visibility: the role, guild admins and the bot."""
        ...

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        """Deliver a UI SDK layout to the channel; the message id."""
        ...


class AdminChannelDatabase(Protocol):
    """Narrow persistence seam (the shared channels collection)."""

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        """Find the persisted channel document for a guild category."""
        ...

    async def upsert_channel(self, channel: ChannelModel) -> None:
        """Insert or replace the channel document (idempotent provisioning)."""
        ...

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        """Drop a stale channel document; True when one was removed."""
        ...


class AdminChannelService:
    """Provision the 🛡-bot-admins channel and keep the role roster synced."""

    def __init__(
        self,
        platform: AdminChannelPlatform,
        database: AdminChannelDatabase,
        roles_service: RolesService,
        state: Any = None,
        cache_ttl: int = 300,
    ) -> None:
        self._platform = platform
        self._db = database
        self._roles = roles_service
        self._state = state
        self._cache_ttl = cache_ttl

    async def _safe(self, coro: Any) -> Any:
        try:
            return await coro
        except Exception:
            logger.warning("ADMIN CHANNEL cache read failed — cache-aside continues", exc_info=True)
            return None

    async def _cache_key(self, guild_id: str) -> str:
        return f"admin_channel:{guild_id}"

    async def _cache_get(self, guild_id: str) -> str | None:
        if self._state is None:
            return None
        cached = await self._safe(self._state.get_state("channels", await self._cache_key(guild_id)))
        return str(cached.get("channel_id")) if cached and cached.get("channel_id") else None

    async def _cache_set(self, guild_id: str, channel_id: str) -> None:
        if self._state is None:
            return
        await self._safe(
            self._state.set_state(
                "channels", await self._cache_key(guild_id), {"channel_id": channel_id}, ttl=self._cache_ttl
            )
        )

    async def _cache_drop(self, guild_id: str) -> None:
        if self._state is None:
            return
        await self._safe(self._state.delete_state("channels", await self._cache_key(guild_id)))

    async def resolve_channel(self, guild_id: str, admin_ids: tuple[str, ...] = ()) -> str | None:
        """Cache-aside resolution: Redis → Mongo → adoption → creation.

        Every resolution also refreshes the transparency contract: the
        admin role is provisioned, the BOT_ADMINS synced into it, and
        the channel policy reapplied — a manual un-sync never survives
        a resolution.
        """
        if not guild_id:
            return None
        cached = await self._cache_get(guild_id)
        if cached and await self._platform.channel_exists(guild_id, cached):
            await self._sync_transparency(guild_id, cached, admin_ids)
            return cached
        stored = await self._db.find_channel(guild_id, ADMIN_CHANNEL_CATEGORY)
        if stored is not None:
            if await self._platform.channel_exists(guild_id, stored.channel_id):
                await self._cache_set(guild_id, stored.channel_id)
                await self._sync_transparency(guild_id, stored.channel_id, admin_ids)
                return stored.channel_id
            await self._db.delete_channel(guild_id, ADMIN_CHANNEL_CATEGORY)
        adopted = await self._platform.find_channel_by_name(guild_id, ADMIN_CHANNEL_NAME)
        if adopted is not None:
            await self._db.upsert_channel(
                ChannelModel(
                    _id=f"{guild_id}:{ADMIN_CHANNEL_CATEGORY}",
                    guild_id=guild_id,
                    platform="discord",
                    category=ADMIN_CHANNEL_CATEGORY,
                    channel_id=adopted,
                    name=ADMIN_CHANNEL_NAME,
                )
            )
            await self._cache_set(guild_id, adopted)
            await self._sync_transparency(guild_id, adopted, admin_ids)
            return adopted
        channel_id = await self._platform.create_channel(
            guild_id,
            ADMIN_CHANNEL_NAME,
            reason="Kingdoms admin channel (kingdoms-services#115)",
        )
        await self._db.upsert_channel(
            ChannelModel(
                _id=f"{guild_id}:{ADMIN_CHANNEL_CATEGORY}",
                guild_id=guild_id,
                platform="discord",
                category=ADMIN_CHANNEL_CATEGORY,
                channel_id=channel_id,
                name=ADMIN_CHANNEL_NAME,
            )
        )
        await self._cache_set(guild_id, channel_id)
        await self._sync_transparency(guild_id, channel_id, admin_ids)
        return channel_id

    async def _sync_transparency(self, guild_id: str, channel_id: str, admin_ids: tuple[str, ...]) -> None:
        """Apply the transparency contract: role provisioned, admins synced, policy applied."""
        try:
            role_id = await self._roles.provision_admin_role(guild_id)
            if admin_ids:
                await self._platform.sync_admin_role_members(guild_id, role_id, admin_ids)
            await self._platform.apply_channel_policy(guild_id, channel_id, role_id)
        except Exception:
            logger.warning(
                "ADMIN CHANNEL transparency sync failed (guild %s, channel %s) — best-effort",
                guild_id,
                channel_id,
                exc_info=True,
            )

    async def deliver(self, guild_id: str, layout: Any, admin_ids: tuple[str, ...] = ()) -> str | None:
        """Deliver an admin message (a UI SDK layout) to the admin channel.

        Resolves the channel first (provisioning included), then hands
        the layout to the platform seam. Best-effort: a delivery failure
        never propagates — the admin surface degrades to the ephemeral
        answers.
        """
        channel_id = await self.resolve_channel(guild_id, admin_ids)
        if channel_id is None:
            return None
        try:
            return await self._platform.send_layout(guild_id, channel_id, layout)
        except Exception:
            logger.warning(
                "ADMIN CHANNEL delivery failed (guild %s, channel %s) — best-effort",
                guild_id,
                channel_id,
                exc_info=True,
            )
            return None
