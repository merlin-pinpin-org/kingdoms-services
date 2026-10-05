"""Platform identity service (kingdoms-services#130, reference §1.1).

Maps internal ``user_id``s to platform accounts and back. Identities are
multi-platform by design: one Kingdoms user may be linked to a Discord
account today and another platform tomorrow, so the mapping lives in its
own collection, never inside the mod data. Mods reference users by
``user_id`` only — platform-specific account data stays here.

Resolution is cache-aside (Redis → MongoDB), like the other platform
services; user creation is idempotent per ``(platform, platform_user_id)``.
"""

from __future__ import annotations

import logging
from typing import Protocol

from kingdoms.core.models.user import UserModel

logger = logging.getLogger("kingdoms.core.identity")

IDENTITY_COLLECTION = "platform_identities"
USERS_COLLECTION = "users"
IDENTITY_CACHE_TTL_SECONDS = 3600


class IdentityDatabase(Protocol):
    """Narrow async MongoDB seam the IdentityService depends on."""

    async def find_identity(self, platform: str, platform_user_id: str) -> str | None:
        """Return the internal user_id bound to a platform account; None when unbound."""
        ...

    async def bind_identity(self, platform: str, platform_user_id: str, user_id: str) -> None:
        """Persist the (platform, account) → user_id binding (upsert)."""
        ...

    async def unbind_identity(self, platform: str, platform_user_id: str) -> bool:
        """Remove a binding; True when one was removed."""
        ...

    async def find_user(self, user_id: str) -> UserModel | None:
        """Return the internal user document; None when absent."""
        ...

    async def upsert_user(self, user: UserModel) -> None:
        """Insert or replace the user document."""
        ...


class IdentityCache(Protocol):
    """Narrow cache seam (StateService); values are JSON dicts."""

    async def get_state(self, scope: str, key: str) -> dict[str, object] | None:
        """Read one cached value; None on miss (store may be down)."""
        ...

    async def set_state(
        self, scope: str, key: str, value: dict[str, object], ttl: int | None = None
    ) -> bool:
        """Write one cached value with a TTL (best-effort); True when written."""
        ...

    async def delete_state(self, scope: str, key: str) -> bool:
        """Drop one cached value (best-effort)."""
        ...


class IdentityService:
    """Resolve internal user ids to platform accounts and back."""

    def __init__(self, database: IdentityDatabase, cache: IdentityCache) -> None:
        """Wire the persistence and cache seams."""
        self._database = database
        self._cache = cache

    async def resolve_user_id(self, platform: str, platform_user_id: str) -> str | None:
        """Return the internal user_id bound to a platform account; None when unknown."""
        cached = await self._read_cache(platform, platform_user_id)
        if cached is not None:
            return cached
        user_id = await self._database.find_identity(platform, platform_user_id)
        if user_id is None:
            return None
        await self._store_cache(platform, platform_user_id, user_id)
        return user_id

    async def get_or_create_user(
        self,
        *,
        platform: str,
        platform_user_id: str,
        display_name: str,
        locale: str = "en",
    ) -> UserModel:
        """Return the user bound to a platform account, creating it when unknown."""
        existing = await self.resolve_user_id(platform, platform_user_id)
        if existing is not None:
            user = await self._database.find_user(existing)
            if user is not None:
                return user
            return await self._create_user(platform, platform_user_id, existing, display_name, locale)
        user_id = f"{platform}:{platform_user_id}"
        return await self._create_user(platform, platform_user_id, user_id, display_name, locale)

    async def get_user(self, user_id: str) -> UserModel | None:
        """Return the internal user document; None when absent."""
        return await self._database.find_user(user_id)

    async def _create_user(
        self, platform: str, platform_user_id: str, user_id: str, display_name: str, locale: str
    ) -> UserModel:
        user = UserModel(
            _id=user_id,
            platform=platform,
            platform_user_id=platform_user_id,
            display_name=display_name,
            locale=locale,
        )
        await self._database.upsert_user(user)
        await self._database.bind_identity(platform, platform_user_id, user_id)
        await self._store_cache(platform, platform_user_id, user_id)
        return user

    async def _read_cache(self, platform: str, platform_user_id: str) -> str | None:
        try:
            cached = await self._cache.get_state(IDENTITY_COLLECTION, f"{platform}:{platform_user_id}")
        except Exception:
            logger.warning("IDENTITY CACHE READ FAILED (%s:%s)", platform, platform_user_id)
            return None
        if cached is None or "user_id" not in cached:
            return None
        return str(cached["user_id"])

    async def _store_cache(self, platform: str, platform_user_id: str, user_id: str) -> None:
        """Cache one resolution with the standard TTL (best-effort)."""
        try:
            await self._cache.set_state(
                IDENTITY_COLLECTION,
                f"{platform}:{platform_user_id}",
                {"user_id": user_id},
                IDENTITY_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.warning("IDENTITY CACHE WRITE FAILED (%s:%s)", platform, platform_user_id)
