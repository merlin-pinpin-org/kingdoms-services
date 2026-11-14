"""Core roles service: provision and resolve the platform roles.

The ``bot-admins`` guild role (botforge-services#115) delegates bot
administration to chosen members without handing them the Discord
guild administrator permission: the runtime guards
(:mod:`botforge.discord.guards`) validate it at click time.

The service follows the LogService provisioning pattern
(cache-aside): the resolved role id is cached in Redis with a TTL,
falls back to the platform lookup, and the role is created when
missing. A role renamed away from ``bot-admins`` resolves as absent —
delegated admin access is lost by design until the role is restored.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Protocol

from botforge.core.models.role_mapping import RoleMappingModel

if TYPE_CHECKING:
    from botforge.core.services.mod_definition import RoleDef
    from botforge.core.services.mod_registry import ModRegistry

logger = logging.getLogger("botforge.core.roles")

ADMIN_ROLE_NAME = "bot-admins"
ROLE_CACHE_TTL_SECONDS = 300
CACHE_SCOPE = "roles"


class RolesPlatform(Protocol):
    """Narrow platform seam: role lookup, creation and deletion."""

    async def delete_role(self, guild_id: str, role_id: str, reason: str) -> None:
        """Delete one role; missing roles are a no-op."""
        ...

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        """Find a guild role id by its exact name; None when absent."""
        ...

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        """Create a guild role; return its id."""
        ...

    async def role_exists(self, guild_id: str, role_id: str) -> bool:
        """Whether a role id still lives on the platform."""
        ...


class RolesCache(Protocol):
    """Narrow cache seam (the StateService get_state/set_state pair)."""

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        """Read a hot-state entry; None when missing or expired."""
        ...

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Write a hot-state entry with a TTL; True when the write landed."""
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
            reason="botforge: provision the bot-admins role (/admin)",
        )
        await self._cache_set(f"roles:admin:{guild_id}", role_id)
        logger.info("BOT-ADMINS ROLE CREATED (guild %s, role %s)", guild_id, role_id)
        return role_id


class RolesDatabase(Protocol):
    """Narrow async MongoDB seam for the mod-role mappings."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database."""
        ...

    async def find_role_mapping(self, guild_id: str, mod: str, role_key: str) -> RoleMappingModel | None:
        """Find the persisted mapping for a guild mod role key."""
        ...

    async def list_role_mappings(self, guild_id: str, mod: str) -> list[RoleMappingModel]:
        """List every persisted mapping of one guild mod (incl. per-season)."""
        ...

    async def upsert_role_mapping(self, mapping: RoleMappingModel) -> None:
        """Insert or replace the role mapping document."""
        ...

    async def delete_role_mapping(self, guild_id: str, mod: str, role_key: str) -> bool:
        """Drop a role mapping document; True when one was removed."""
        ...


class ModRolesPlatform(Protocol):
    """Narrow platform seam for member role assignment."""

    async def add_role_to_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        """Add a role to a member (audited through ``reason``)."""
        ...

    async def remove_role_from_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        """Remove a role from a member (audited through ``reason``)."""
        ...


ROLE_MAPPINGS_COLLECTION = "role_mappings"
MOD_ROLE_CACHE_TTL_SECONDS = 300
MOD_ROLE_CACHE_SCOPE = "roles:mod"


class ModRolesService:
    """Provision and resolve the mod-declared roles (logical role keys).

    Mods and game providers only ever reference logical role keys
    (``mod:role_key``); the platform role id is resolved through the
    persisted mapping (botforge-services#26) with a fallback to the
    declared display name, and admins can rebind a key to an existing
    platform role without touching mod code.
    """

    def __init__(
        self,
        database: RolesDatabase,
        platform: RolesPlatform,
        registry: ModRegistry,
        members: ModRolesPlatform,
        cache: RolesCache | None = None,
    ) -> None:
        """Wire the stores; ``cache`` is a StateService (Redis cache-aside)."""
        self._db = database
        self._platform = platform
        self._registry = registry
        self._members = members
        self._cache = cache

    def _cache_key(self, guild_id: str, mod: str, role_key: str) -> str:
        """Build the cache key for one logical role key."""
        return f"{guild_id}:{mod}:{role_key}"

    async def _cache_get(self, key: str) -> str | None:
        """Read one cached role id (best-effort; None on miss)."""
        if self._cache is None:
            return None
        try:
            entry = await self._cache.get_state(MOD_ROLE_CACHE_SCOPE, key)
        except Exception:
            return None
        role_id = str((entry or {}).get("role_id", ""))
        return role_id or None

    async def _cache_set(self, key: str, role_id: str) -> None:
        """Cache one role id (best-effort)."""
        if self._cache is None:
            return
        try:
            await self._cache.set_state(MOD_ROLE_CACHE_SCOPE, key, {"role_id": role_id}, ttl=MOD_ROLE_CACHE_TTL_SECONDS)
        except Exception:
            logger.warning("MOD-ROLE CACHE SET FAILED (key %s) — cache-aside continues uncached", key)

    async def setup_mod_roles(self, guild_id: str, mod_name: str) -> dict[str, str]:
        """Provision every role declared by a mod; return key -> role id.

        Idempotent: an existing role with the same display name is reused,
        the missing ones are created, and every declared key is persisted
        as a mapping so runtime resolution never depends on the display
        name alone.
        """
        definition = self._registry.require(mod_name)
        resolved: dict[str, str] = {}
        for role_def in definition.roles:
            role_id = await self._resolve_or_provision(guild_id, mod_name, role_def)
            resolved[role_def.key] = role_id
        return resolved

    async def teardown_mod_roles(self, guild_id: str, mod_name: str) -> int:
        """Delete every role a mod provisioned (uninstall with the choice).

        The persisted mappings go with the roles: a reinstall provisions
        fresh (the roles are the mod's, the mappings are its ledger).
        Best-effort per role; returns how many were deleted.
        """
        deleted = 0
        for mapping in await self._db.list_role_mappings(guild_id, mod_name):
            try:
                await self._delete_platform_role(guild_id, mapping.role_id, mod_name)
                deleted += 1
            except Exception:
                logger.warning(
                    "mod roles teardown: deleting %s failed — best-effort", mapping.role_id, exc_info=True
                )
            await self._db.delete_role_mapping(guild_id, mod_name, mapping.role_key)
        return deleted

    async def _delete_platform_role(self, guild_id: str, role_id: str, mod_name: str) -> None:
        """Delete one platform role (the platform seam owns the how)."""
        await self._platform.delete_role(
            guild_id, role_id, reason=f"botforge: uninstall the {mod_name} mod"
        )

    async def _resolve_or_provision(self, guild_id: str, mod_name: str, role_def: RoleDef) -> str:
        """Resolve one declared role to a platform role id, creating it when missing."""
        existing = await self._db.find_role_mapping(guild_id, mod_name, role_def.key)
        if existing is not None and await self._platform.role_exists(guild_id, existing.role_id):
            await self._cache_set(self._cache_key(guild_id, mod_name, role_def.key), existing.role_id)
            return existing.role_id
        # the mapped role is gone (deleted on Discord): re-adopt by name
        # or re-create — the mapping heals, the declared roles always live
        adopted = await self._platform.find_role_by_name(guild_id, role_def.display_name)
        if adopted is not None:
            await self._persist_mapping(guild_id, mod_name, role_def.key, adopted)
            return adopted
        created = await self._platform.create_role(
            guild_id,
            role_def.display_name,
            reason=f"botforge: provision the {mod_name} mod role {role_def.key}",
        )
        await self._persist_mapping(guild_id, mod_name, role_def.key, created)
        return created

    async def _persist_mapping(self, guild_id: str, mod_name: str, role_key: str, role_id: str) -> None:
        """Persist (and cache) one logical-key to platform-role mapping."""
        await self._db.upsert_role_mapping(
            RoleMappingModel(
                _id=f"{guild_id}:{mod_name}:{role_key}",
                guild_id=guild_id,
                mod=mod_name,
                role_key=role_key,
                role_id=role_id,
            )
        )
        await self._cache_set(self._cache_key(guild_id, mod_name, role_key), role_id)

    async def find_role_mapping(self, guild_id: str, mod: str, role_key: str) -> RoleMappingModel | None:
        """Return the persisted mapping for one logical role key (ledger only).

        The render-side resolver (owner rule 2026-11-13: never a name
        match): None means the ledger holds no entry — the caller shows
        the honest state, the repair provisions and persists.
        """
        return await self._db.find_role_mapping(guild_id, mod, role_key)

    async def bind_role(self, guild_id: str, mod: str, role_key: str, role_id: str) -> None:
        """Create or update the mapping between a logical role key and a platform role.

        Admins rebind a logical role to an existing platform role (e.g. a
        server role created before the bot) without touching mod code.
        """
        await self._persist_mapping(guild_id, mod, role_key, role_id)

    async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
        """Resolve a logical role key to a platform role id.

        Resolution order: cache -> mapping -> declared display name.
        An unknown mod or undeclared key fails loudly at the registry,
        but a declared key with no mapping yet falls back to the display
        name (auto-provisioning in progress or in progress of a setup).
        """
        cache_key = self._cache_key(guild_id, mod, role_key)
        cached = await self._cache_get(cache_key)
        if cached:
            return cached
        mapping = await self._db.find_role_mapping(guild_id, mod, role_key)
        if mapping is not None:
            await self._cache_set(cache_key, mapping.role_id)
            return mapping.role_id
        definition = self._registry.require(mod)
        role_def = definition.role(role_key)
        role_id = await self._platform.find_role_by_name(guild_id, role_def.display_name)
        if role_id:
            await self._cache_set(cache_key, role_id)
        return role_id

    async def assign_mod_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> None:
        """Assign the platform role mapped to a logical role key to a member."""
        role_id = await self.resolve_role_id(guild_id, mod, role_key)
        if role_id is None:
            raise LookupError(f"No platform role mapped for {mod}:{role_key} in guild {guild_id}")
        await self._members.add_role_to_member(
            guild_id, user_id, role_id, reason=f"botforge: assign mod role {mod}:{role_key}"
        )

    async def remove_mod_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> None:
        """Remove the platform role mapped to a logical role key from a member."""
        role_id = await self.resolve_role_id(guild_id, mod, role_key)
        if role_id is None:
            return
        await self._members.remove_role_from_member(
            guild_id, user_id, role_id, reason=f"botforge: remove mod role {mod}:{role_key}"
        )

    async def adopt_or_create_role(self, guild_id: str, mod_name: str, role_def: RoleDef) -> str | None:
        """Resolve one role to a platform id, adopting or creating it.

        The runtime equivalent of ``_resolve_or_provision`` for keys a
        season invents at runtime (not declared in the mod YAML): an
        existing role with the display name is adopted, otherwise the
        role is created — and the mapping is persisted either way.
        """
        try:
            return await self._resolve_or_provision(guild_id, mod_name, role_def)
        except Exception:
            logger.warning("MOD-ROLE provisioning failed (%s:%s)", mod_name, role_def.key, exc_info=True)
            return None

    async def assign_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        """Assign one platform role id to a member (best-effort)."""
        try:
            await self._members.add_role_to_member(
                guild_id, user_id, role_id, reason=f"botforge: assign mod role {mod}:{role_key}"
            )
        except Exception:
            logger.warning(
                "MOD-ROLE assign failed (guild %s, user %s, role %s)", guild_id, user_id, role_id, exc_info=True
            )

    async def remove_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        """Remove one platform role id from a member (best-effort)."""
        try:
            await self._members.remove_role_from_member(
                guild_id, user_id, role_id, reason=f"botforge: remove mod role {mod}:{role_key}"
            )
        except Exception:
            logger.warning(
                "MOD-ROLE remove failed (guild %s, user %s, role %s)", guild_id, user_id, role_id, exc_info=True
            )
