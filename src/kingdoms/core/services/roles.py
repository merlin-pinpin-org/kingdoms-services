"""Core roles service: provision and resolve the platform roles.

The ``bot-admins`` guild role (kingdoms-services#115) delegates bot
administration to chosen members without handing them the Discord
guild administrator permission: the runtime guards
(:mod:`kingdoms.discord.guards`) validate it at click time.

The service follows the LogService provisioning pattern
(cache-aside): the resolved role id is cached in Redis with a TTL,
falls back to the platform lookup, and the role is created when
missing. A role renamed away from ``bot-admins`` resolves as absent —
delegated admin access is lost by design until the role is restored.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.roles")

ADMIN_ROLE_NAME = "bot-admins"
ROLE_CACHE_TTL_SECONDS = 300
CACHE_SCOPE = "roles"


class RolesPlatform(Protocol):
    """Narrow platform seam: role lookup and creation."""

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild role id by its exact name; None when absent."""
        ...

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        """Create a guild role; return its id."""
        ...


class RolesCache(Protocol):
    """Narrow cache seam (the StateService get_state/set_state pair)."""

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read a hot-state entry; None when missing or expired."""
        ...

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> None:
        """Write a hot-state entry with a TTL in seconds."""
        ...


class RolesService:
    """Provision and resolve the platform roles, cache-aside."""

    def __init__(
        self,
        platform: RolesPlatform,
        cache: RolesCache | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._platform = platform
        self._cache = cache
        self._clock = clock

    async def _cache_get(self, guild_id: str) -> str | None:
        if self._cache is None:
            return None
        try:
            entry = await self._cache.get_state(CACHE_SCOPE, guild_id)
        except Exception:
            return None
        role_id = str((entry or {}).get("role_id", ""))
        return role_id or None

    async def _cache_set(self, guild_id: str, role_id: str) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.set_state(CACHE_SCOPE, guild_id, {"role_id": role_id}, ttl=ROLE_CACHE_TTL_SECONDS)
        except Exception:
            logger.warning("ROLES CACHE SET FAILED — cache-aside continues uncached")

    async def resolve_admin_role(self, guild_id: str) -> str | None:
        """Resolve the guild's bot-admins role id (cache → platform → None)."""
        if not guild_id:
            return None
        cached = await self._cache_get(guild_id)
        if cached:
            return cached
        role_id = await self._platform.find_role_by_name(guild_id, ADMIN_ROLE_NAME)
        if role_id:
            await self._cache_set(guild_id, role_id)
        return role_id

    async def provision_admin_role(self, guild_id: str) -> str:
        """Ensure the guild has its bot-admins role; return the role id.

        Idempotent: the existing role is reused, the missing one is
        created. The cache is refreshed either way — a provision is
        also the invalidation point after a rename.
        """
        existing = await self._platform.find_role_by_name(guild_id, ADMIN_ROLE_NAME)
        if existing:
            await self._cache_set(guild_id, existing)
            return existing
        role_id = await self._platform.create_role(
            guild_id,
            ADMIN_ROLE_NAME,
            reason="kingdoms: provision the bot-admins role (/admin)",
        )
        await self._cache_set(f"roles:admin:{guild_id}", role_id)
        logger.info("BOT-ADMINS ROLE CREATED (guild %s, role %s)", guild_id, role_id)
        return role_id
