"""Mod lifecycle: the per-guild active/suspended state (botforge-services#267).

A suspended mod is **managed but unusable**: the admins keep every
management surface (the admin panel, the console, the enrollment
dashboard — deciding pending applications, ending seasons), while the
players cannot use it — the user-facing flows and the enrollment
refuse with one clear answer. Suspension never destroys anything: it
is a flag, not a wipe.

The state is per guild: the same mod may run on one guild and be
suspended on another. Document id: ``<guild_id>:<mod>``.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("botforge.core.mod_lifecycle")

MOD_STATES_COLLECTION = "mod_states"

STATE_ACTIVE = "active"
STATE_SUSPENDED = "suspended"


class ModSuspendedError(RuntimeError):
    """The mod is suspended on this guild — the players cannot use it."""

    def __init__(self, mod: str, guild_id: str, reason: str = "") -> None:
        """Carry the refusal's context for the surface's message."""
        detail = f" (raison : {reason})" if reason else ""
        super().__init__(f"mod {mod!r} is suspended on guild {guild_id}{detail}")
        self.mod = mod
        self.guild_id = guild_id
        self.reason = reason


class ModLifecycleDatabase(Protocol):
    """Narrow async MongoDB seam the ModLifecycleService depends on."""

    async def upsert_mod_state(self, document: dict[str, Any]) -> None:
        """Insert or replace one mod state document by ``_id``."""
        ...

    async def find_mod_state(self, guild_id: str, mod: str) -> dict[str, Any] | None:
        """Return one mod state document; None when absent."""
        ...


class ModLifecycleService:
    """Suspend and resume a mod on one guild (admins manage, players wait)."""

    def __init__(self, database: ModLifecycleDatabase) -> None:
        """Wire the narrow persistence seam."""
        self._db = database

    async def suspend(self, guild_id: str, mod: str, now: int = 0, reason: str = "") -> dict[str, Any]:
        """Suspend the mod on the guild (idempotent; the reason refreshes)."""
        doc = {
            "_id": f"{guild_id}:{mod}",
            "guild_id": guild_id,
            "mod": mod,
            "state": STATE_SUSPENDED,
            "suspended_at": now,
            "reason": reason,
        }
        await self._db.upsert_mod_state(doc)
        logger.info("MOD %s SUSPENDED (guild %s)%s", mod, guild_id, f" — {reason}" if reason else "")
        return doc

    async def resume(self, guild_id: str, mod: str, now: int = 0) -> dict[str, Any]:
        """Resume the mod (idempotent; a fresh state document)."""
        doc = {
            "_id": f"{guild_id}:{mod}",
            "guild_id": guild_id,
            "mod": mod,
            "state": STATE_ACTIVE,
            "suspended_at": 0,
            "resumed_at": now,
            "reason": "",
        }
        await self._db.upsert_mod_state(doc)
        logger.info("MOD %s RESUMED (guild %s)", mod, guild_id)
        return doc

    async def state(self, guild_id: str, mod: str) -> str:
        """Return the mod's state on the guild: active (default) or suspended."""
        doc = await self._db.find_mod_state(guild_id, mod)
        if doc is None:
            return STATE_ACTIVE
        return str(doc.get("state") or STATE_ACTIVE)

    async def is_suspended(self, guild_id: str, mod: str) -> bool:
        """Whether the mod is suspended on the guild."""
        return await self.state(guild_id, mod) == STATE_SUSPENDED

    async def require_usable(self, guild_id: str, mod: str) -> None:
        """Raise ModSuspendedError when the players cannot use the mod.

        The enforcement point for every user-facing flow: the admin
        surfaces never call this — an admin manages a suspended mod.
        """
        doc = await self._db.find_mod_state(guild_id, mod)
        if doc is not None and doc.get("state") == STATE_SUSPENDED:
            raise ModSuspendedError(mod, guild_id, str(doc.get("reason") or ""))
