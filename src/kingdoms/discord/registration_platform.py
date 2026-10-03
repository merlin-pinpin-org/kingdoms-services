"""Discord-side adapters for the registration stack (kingdoms-services#133).

Production wiring of the RegistrationService seams: the Mongo adapter
for the game-side profile bindings, and the AoE2 profile-validation
seam — a profile id is valid when the provider (ext-librematch via the
kingdoms.v1.Game contract) can resolve its stats.
"""

from __future__ import annotations

import os
from typing import Any

from kingdoms.core.services.registration import (
    PROFILE_BINDINGS_COLLECTION,
)


class MongoRegistrationDatabase:
    """Async MongoDB adapter for the RegistrationService persistence seam."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        await self._database[collection].replace_one({"_id": document["_id"]}, document, upsert=True)

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        doc = await self._database[collection].find_one({"_id": entry_id})
        return dict(doc) if doc is not None else None

    async def find_user_bindings(self, user_id: str) -> list[dict[str, Any]]:
        """List the game-profile bindings of a user (all games)."""
        cursor = self._database[PROFILE_BINDINGS_COLLECTION].find({"user_id": user_id})
        return [dict(doc) async for doc in cursor]

    async def find_binding_by_profile(self, game_key: str, profile_id: str) -> dict[str, Any] | None:
        """Return the binding owning a profile; None when unbound."""
        doc = await self._database[PROFILE_BINDINGS_COLLECTION].find_one(
            {"game_key": game_key, "profile_id": profile_id}
        )
        return dict(doc) if doc is not None else None

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, Any]]:
        """List every profile binding of a game (live dashboard reuse, #147)."""
        cursor = self._database[PROFILE_BINDINGS_COLLECTION].find({"game_key": game_key})
        return [dict(doc) async for doc in cursor]


class Aoe2ProfileValidationSeam:
    """AoE2 profile validation through the provider (kingdoms.v1.Game).

    A profile id is valid when the configured provider can resolve its
    stats. With no provider URI (local runs), every id is rejected —
    validation is the whole point of the binding (reference §0/§6).
    """

    def __init__(self, provider_uri: str | None = None) -> None:
        """Read the provider address from the environment by default."""
        self._provider_uri = provider_uri or os.environ.get("EXT_LIBREMATCH_URI", "")

    async def validate_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Return the profile's public data when the provider knows it."""
        if not self._provider_uri:
            return None
        from kingdoms.core.rpc.game_client import GameProviderClient

        client = GameProviderClient(self._provider_uri, "ext-librematch", "aoe2")
        stats = await client.get_player_stats(profile_id)
        if stats is None:
            return None
        return {
            "profile_id": profile_id,
            "aliases": [e.value for b in stats.blocks for e in b.entries][:5],
        }
