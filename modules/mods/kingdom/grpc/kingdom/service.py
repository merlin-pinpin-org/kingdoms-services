"""The kingdom mod's service (§7 grpc): its data, its collections.

Implements the contract (contract.py). Never imports Discord — the cog
is the mod's only Discord layer.
"""

from __future__ import annotations

from typing import Any

from .contract import KingdomService as KingdomContract


class KingdomServiceImpl(KingdomContract):
    """The kingdom mod's service implementation (in-process today)."""

    async def status(self, guild_id: str) -> dict[str, Any]:
        """Return the mod's status for the guild (the cog renders it)."""
        return {"mod": "kingdom", "guild": guild_id, "state": "scaffold"}
