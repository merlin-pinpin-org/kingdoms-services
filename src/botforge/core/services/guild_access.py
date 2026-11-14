"""GuildAccessService: which guild may use which games and mods (core).

A bot admin is cross-guild: they administer the platform from DMs,
not from any single guild's panel. By default the **default-active**
items are on: a guild with no access document gets the declarations'
default-active sets (games active, mods not — the future private-mod
seam is a mod needing explicit activation, like kingdoms). A stored
access doc is the whole truth, even empty: an uninstall sticks.

Storage: the ``guild_access`` Mongo collection, one document per guild
(``guild:<id>``) carrying ``games`` and ``mods`` (lists of granted
keys) plus ``pending`` requests. Requests and grants are auditable
state, not config: they survive restarts and deployments.

Semantics: a guild with NO document gets the default-active sets (the
declarations' ``default_active`` flags — games default active, mods
default inactive). Once a document exists its lists are the whole
truth, EVEN EMPTY: uninstalling the last item sticks (the catalog's
Install button brings it back), it does not silently re-open the
defaults.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

logger = logging.getLogger("botforge.core.guild_access")

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
        default_games: tuple[str, ...] | None = None,
        default_mods: tuple[str, ...] | None = None,
    ) -> None:
        """Store the seams and the platform's known games and mods.

        ``games``/``mods`` restrict what a guild may be granted: only
        keys the platform actually ships (config) can be approved.
        """
        self._db = database
        self._games = tuple(games)
        self._mods = tuple(mods)
        # the default-active sets (per-declaration flags): what a guild
        # with NO access document gets — games default active, mods
        # default inactive (owner direction 2026-11-11)
        self._default_games = tuple(default_games) if default_games is not None else tuple(games)
        self._default_mods = tuple(default_mods) if default_mods is not None else ()

    async def get(self, guild_id: str) -> dict[str, Any]:
        """Return one guild's access state.

        No document: the synthetic default state — the default-active
        sets materialized — so the write paths (request/grant/approve)
        persist a complete document on first touch.
        """
        doc = await self._db.get_guild_access(guild_id)
        if doc is None:
            return {
                "guild_id": guild_id,
                "games": list(self._default_games),
                "mods": list(self._default_mods),
                "pending": {},
            }
        return doc

    async def enabled_games(self, guild_id: str) -> tuple[str, ...]:
        """List the games active for one guild.

        No access document: the default-active games. A stored document
        is the whole truth — an empty list means NONE active (an
        uninstall sticks; the catalog's Install button reinstalls).
        """
        doc = await self._db.get_guild_access(guild_id)
        if doc is None:
            return self._default_games
        return tuple(doc.get("games") or ())

    async def enabled_mods(self, guild_id: str) -> tuple[str, ...]:
        """List the mods active for one guild (default: none active)."""
        doc = await self._db.get_guild_access(guild_id)
        if doc is None:
            return self._default_mods
        return tuple(doc.get("mods") or ())

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
        already_granted: list[str] = []
        already_pending: list[str] = []
        for key in keys:
            if key.startswith("game:"):
                granted = key[5:] in games
            else:
                granted = key[4:] in mods
            if granted:
                already_granted.append(key)
                continue
            if key in [k for ks in pending.values() for k in ks]:
                already_pending.append(key)
                continue
            fresh.append(key)
        if not fresh:
            if already_granted:
                raise GuildAccessError(
                    "deja accorde : " + ", ".join(already_granted)
                )
            raise GuildAccessError("deja en attente : " + ", ".join(already_pending))
        pending[str(int(time.time()))] = fresh
        doc["pending"] = pending
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def grant(self, guild_id: str, keys: list[str]) -> dict[str, Any]:
        """Grant games/mods to a guild directly (bot admin, no request).

        Keys carry their kind (``game:<key>`` / ``mod:<key>``); a
        mod's declared ``required_games`` ride along (same expansion
        as the request path — a mod is never granted unusable).
        """
        self._validate_keys(keys)
        doc = await self.get(guild_id)
        games = set(doc.get("games") or ())
        mods = set(doc.get("mods") or ())
        for key in keys:
            if key.startswith("game:"):
                games.add(key[5:])
            else:
                mods.add(key[4:])
        doc["games"] = sorted(games)
        doc["mods"] = sorted(mods)
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def set_grants(self, guild_id: str, games: list[str], mods: list[str]) -> dict[str, Any]:
        """Replace one guild's grants wholesale (dev console only).

        Unlike approve/revoke (incremental, audit-driven), this is the
        declarative seam the dev console uses after the install picker:
        the selection is the whole truth. Every key is validated
        against the platform's known games/mods; unknown keys fail
        loudly and the document is untouched.
        """
        self._validate_keys([f"game:{g}" for g in games] + [f"mod:{m}" for m in mods])
        doc = await self.get(guild_id)
        doc["games"] = sorted(set(games))
        doc["mods"] = sorted(set(mods))
        doc["pending"] = {}
        doc["updated_at"] = int(time.time())
        await self._db.upsert_guild_access(self._to_mongo(doc))
        return doc

    async def revoke_keys(self, guild_id: str, keys: list[str]) -> dict[str, Any]:
        """Revoke games/mods from a guild directly (bot admin)."""
        self._validate_keys(keys)
        doc = await self.get(guild_id)
        games = set(doc.get("games") or ())
        mods = set(doc.get("mods") or ())
        for key in keys:
            if key.startswith("game:"):
                games.discard(key[5:])
            else:
                mods.discard(key[4:])
        doc["games"] = sorted(games)
        doc["mods"] = sorted(mods)
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

    async def ensure_known(self, guild_id: str) -> dict[str, Any]:
        """Make sure one guild the bot sits in has an access document.

        The panel's server list reads the collection, but documents were
        only ever written by a request or a grant \u2014 a database reset
        showed 0 servers while the bot kept interacting with its guilds.
        The boot (and every guild join) re-creates the default document
        (the default-active sets materialized — semantically identical
        to no document at all), so the stored list and the bot's guilds
        stay coherent. An existing document is returned untouched.
        """
        doc = await self._db.get_guild_access(guild_id)
        if doc is not None:
            return doc
        fresh = {
            "guild_id": guild_id,
            "games": sorted(self._default_games),
            "mods": sorted(self._default_mods),
            "pending": {},
            "updated_at": int(time.time()),
        }
        await self._db.upsert_guild_access(self._to_mongo(fresh))
        return fresh

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
