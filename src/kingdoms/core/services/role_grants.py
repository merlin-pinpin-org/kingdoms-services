"""Role-grant mappings: which guild roles carry which bot function.

A bot function (``bot-admins``, ``staff``...) can be carried by any
guild role, not just the provisioned defaults: a guild may map its
native admin role, or its VIP role, onto a function. Storage is one
document per guild in the ``role_grants`` collection:

``{"_id": "<guild>", "functions": {"bot-admins": ["<role id>"], ...}}``

An empty list falls back to the function's default (the provisioned
role). Checks are additive: BOT_ADMINS and guild administrators always
pass, the mapped roles add on top.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.role_grants")

ROLE_GRANTS_COLLECTION = "role_grants"
FUNCTION_BOT_ADMINS = "bot-admins"
FUNCTION_STAFF = "staff"
FUNCTIONS = (FUNCTION_BOT_ADMINS, FUNCTION_STAFF)


class RoleGrantsDatabase(Protocol):
    """Narrow async MongoDB seam for the role-grant documents."""

    async def find_grants(self, guild_id: str) -> dict[str, Any] | None:
        """Return the guild's grants document; None when unset."""
        ...

    async def upsert_grants(self, document: dict[str, Any]) -> None:
        """Insert or replace one guild's grants document."""
        ...


class RoleGrantsService:
    """Per-guild role mappings for the bot functions."""

    def __init__(self, database: RoleGrantsDatabase) -> None:
        """Store the persistence seam."""
        self._db = database

    async def function_roles(self, guild_id: str, function: str) -> tuple[str, ...]:
        """List the guild roles carrying one function (empty: default)."""
        if not guild_id or function not in FUNCTIONS:
            return ()
        try:
            doc = await self._db.find_grants(guild_id)
        except Exception:
            logger.warning("ROLE GRANTS read failed (guild %s)", guild_id, exc_info=True)
            return ()
        roles = ((doc or {}).get("functions") or {}).get(function) or []
        return tuple(str(r) for r in roles if str(r).strip())

    async def set_function_roles(self, guild_id: str, function: str, role_ids: tuple[str, ...]) -> None:
        """Replace one function's role list (empty resets to the default)."""
        if function not in FUNCTIONS:
            raise ValueError(f"unknown function {function!r} (expected one of {FUNCTIONS})")
        try:
            doc = await self._db.find_grants(guild_id) or {}
        except Exception:
            doc = {}
        functions = dict(doc.get("functions") or {})
        functions[function] = [str(r) for r in role_ids if str(r).strip()]
        await self._db.upsert_grants(
            {"_id": guild_id, "guild_id": guild_id, "functions": functions}
        )
        logger.info(
            "ROLE GRANTS updated (guild %s, %s -> %d roles)", guild_id, function, len(functions[function])
        )


class MongoRoleGrantsDatabase:
    """Async MongoDB persistence for the role-grant documents."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (get_async_database)."""
        self._collection = database[ROLE_GRANTS_COLLECTION]

    async def find_grants(self, guild_id: str) -> dict[str, Any] | None:
        """Return the guild's grants document; None when unset."""
        doc = await self._collection.find_one({"_id": guild_id})
        return dict(doc) if doc is not None else None

    async def upsert_grants(self, document: dict[str, Any]) -> None:
        """Insert or replace one guild's grants document."""
        await self._collection.replace_one({"_id": document["_id"]}, document, upsert=True)
