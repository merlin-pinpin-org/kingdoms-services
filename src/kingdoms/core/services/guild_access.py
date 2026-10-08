"""GuildAccessService: which guild may use which games and mods (core).

A bot admin is cross-guild: they administer the platform from DMs, not
from any single guild's panel. Conversely, a guild never gets a game or
mod by default — every registered guild must request access, and a bot
admin approves it from the admin DM panel. This is the platform's
isolation seam: nothing is active until explicitly granted.

Storage: the ``guild_access`` Mongo collection, one document per guild
(``guild:<id>``) carrying ``games`` and ``mods`` (lists of granted
keys) plus ``pending`` requests. Requests and grants are auditable
state, not config: they survive restarts and deployments.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.guild_access")

GUILD_ACCESS_COLLECTION = "guild_access"


class GuildAccessDatabase(Protocol):
    """Persistence seam for the per-guild access documents."""

    async def get_guild_access(self, guild_id: str) -> dict[str, Any] | None:
        """Read one guild's access document; None when absent."""
        ...

    async def upsert_guild_access(self, document: dict[str, Any]) -> None:
        """Insert or replace one access document by ``_id``."""
        ...

    async def list_guild_access(self) -> list[dict[str, Any]]:
        """List every guild's access document (for the admin DM panel)."""
        ...


class GuildAccessError(ValueError):
    """A guild-access operation is invalid (unknown game/mod key)."""


class GuildAccessService:
    """Per-guild grants of games and mods: request, approve, revoke."""

    def __init__(
        self,
        database: GuildAccessDatabase,
        games: tuple[str, ...] = (),
        mods: tuple[str, ...] = (),
    ) -> None:
        """Store the seams and the platform's known games and mods.

        ``games``/``mods`` restrict what a guild may be granted: only
        keys the platform actually ships (config) can be approved.
        """
        self._db = database
        self._games = tuple(games)
        self._mods = tuple(mods)

    async def get(self, guild_id: str) -> dict[str, Any]:
        """Return one guild's access state (default: nothing active)."""
        doc = await self._db.get_guild_access(guild_id)
        if doc is None:
            return {"guild_id": guild_id, "games": [], "mods": [], "pending": {}}
        return doc

    async def enabled_games(self, guild_id: str) -> tuple[str, ...]:
        """List the games granted to one guild."""
        access = await self.get(guild_id)
        return tuple(access.get("games") or ())

    async def enabled_mods(self, guild_id: str) -> tuple[str, ...]:
        """List the mods granted to one guild."""
        access = await self.get(guild_id)
        return tuple(access.get("mods") or ())

    async def request_access(self, guild_id: str, keys: list[str]) -> dict[str, Any]:
        """Record a guild's request for games/mods (pending approval).

        Keys carry their kind: ``game:<key>`` or ``mod:<key>``. Unknown
        or already-granted keys are refused loudly.
        """
        self._validate_keys(keys)
        doc = await self.get(guild_id)
        pending: dict[str, list[str]] = dict(doc.get("pending") or {})
        games = set(doc.get("games") or ())
        mods = set(doc.get("mods") or ())
        fresh: list[str] = []
        for key in keys:
            if key.startswith("game:"):
                granted = key[5:] in games
            else:
                granted = key[4:] in mods
            if granted or key in [k for ks in pending.values() for k in ks]:
                continue
            fresh.append(key)
        if not fresh:
            raise GuildAccessError("rien a demander : deja accorde ou deja en attente")
        pending[str(int(time.time()))] = fresh
        doc["pending"] = pending
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def approve(self, guild_id: str, requested_at: str) -> dict[str, Any]:
        """Approve one pending request; the keys become active."""
        doc = await self.get(guild_id)
        pending: dict[str, Any] = dict(doc.get("pending") or {})
        fresh = pending.pop(str(requested_at), None)
        if fresh is None:
            raise GuildAccessError("demande introuvable (deja traitee ?)")
        games = list(doc.get("games") or ())
        mods = list(doc.get("mods") or ())
        for key in fresh:
            if key.startswith("game:") and key[5:] not in games:
                games.append(key[5:])
            elif not key.startswith("game:") and key[4:] not in mods:
                mods.append(key[4:])
        doc["games"] = games
        doc["mods"] = mods
        doc["pending"] = pending
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def deny(self, guild_id: str, requested_at: str) -> dict[str, Any]:
        """Drop one pending request without granting anything."""
        doc = await self.get(guild_id)
        pending: dict[str, Any] = dict(doc.get("pending") or {})
        if pending.pop(str(requested_at), None) is None:
            raise GuildAccessError("demande introuvable (deja traitee ?)")
        doc["pending"] = pending
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def revoke(self, guild_id: str, key: str) -> dict[str, Any]:
        """Revoke one granted game or mod from a guild."""
        self._validate_keys([key])
        doc = await self.get(guild_id)
        if key.startswith("game:"):
            games = [g for g in doc.get("games") or [] if g != key[5:]]
            doc["games"] = games
        else:
            mods = [m for m in doc.get("mods") or [] if m != key[4:]]
            doc["mods"] = mods
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def list_all(self) -> list[dict[str, Any]]:
        """List every guild's access document (the admin DM panel's data)."""
        return list(await self._db.list_guild_access())

    async def pending_requests(self) -> list[dict[str, Any]]:
        """List every guild's pending requests, flattened for the panel."""
        out: list[dict[str, Any]] = []
        for doc in await self.list_all():
            for requested_at, keys in (doc.get("pending") or {}).items():
                out.append(
                    {
                        "guild_id": doc.get("guild_id"),
                        "requested_at": requested_at,
                        "keys": list(keys),
                    }
                )
        return out

    def _validate_keys(self, keys: list[str]) -> None:
        for key in keys:
            if key.startswith("game:"):
                if key[5:] not in self._games:
                    raise GuildAccessError(f"jeu inconnu : {key[5:]!r}")
            elif key.startswith("mod:"):
                if key[4:] not in self._mods:
                    raise GuildAccessError(f"mod inconnu : {key[4:]!r}")
            else:
                raise GuildAccessError(f"cle invalide (game:/mod:) : {key!r}")

    def _to_mongo(self, doc: dict[str, Any]) -> dict[str, Any]:
        return {"_id": f"guild:{doc['guild_id']}", **doc}
