"""The kingdom mod's service contract (§7).

The ONLY surface the cog may call: the service implements it, the cog
consumes it. Everything else in the service package is private — the
cog never touches the mod's collections or internals directly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class KingdomService(Protocol):
    """What the kingdom mod's service exposes to its cog."""

    async def status(self, guild_id: str) -> dict[str, Any]:
        """Return the mod's status for the guild (the cog renders it)."""
        ...
