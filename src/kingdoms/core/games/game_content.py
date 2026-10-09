"""Generic game content source seam (game-agnostic).

Every game-specific content provider (aoe2: the vendored techtree dataset
or the ext-aoe2techtree process) implements **a part** of this generic
game data provider contract. The core consumes content through this seam
only: nothing aoe2-specific (or any other game-specific concern) leaks
here, so the same seam serves the next game's provider unchanged.

Two source kinds implement the seam:

- ``LocalProviderSource`` — a game module's in-process content provider
  (e.g. the vendored dataset extractor).
- ``RpcContentSource`` — a game content provider process over gRPC
  (kingdoms.v1.Content).

Selection is explicit (a game module resolves its own env/config and
hands the result here) — game identity stays in the game module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("kingdoms.core.game_content")


@dataclass(frozen=True, slots=True)
class LocalizedContent:
    """One entity's localized content, ready for a forum post."""

    entity_id: str
    locale: str
    name: str
    summary: str
    source_url: str
    image_url: str = ""


class GameContentSource(Protocol):
    """Localized game content served by an upstream (dataset or API)."""

    async def list_factions(self) -> list[str]:
        """List every faction key known to the source."""

    async def faction_content(self, faction_key: str, locale: str) -> LocalizedContent | None:
        """Return one faction's localized descriptor, None when unknown."""

    async def map_content(self, map_key: str, locale: str) -> LocalizedContent | None:
        """Return one map's localized descriptor, None when unknown."""


class LocalProviderSource:
    """Source = a game module's in-process content provider (default path).

    The wrapped provider is opaque here: it only needs the faction/map
    extraction interface (``faction_names``, ``faction_content``,
    ``map_content``). Game-specific extraction stays in the game module.
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def list_factions(self) -> list[str]:
        """List the provider's faction index."""
        return list(self._provider.faction_names())

    async def faction_content(self, faction_key: str, locale: str) -> LocalizedContent | None:
        """Extract the faction's localized descriptor in-process."""
        content: LocalizedContent | None = self._provider.faction_content(faction_key, locale)
        return content

    async def map_content(self, map_key: str, locale: str) -> LocalizedContent | None:
        """Extract the map's localized descriptor in-process."""
        content: LocalizedContent | None = self._provider.map_content(map_key, locale)
        return content


class RpcContentSource:
    """Source = a game content provider process (kingdoms.v1.Content)."""

    def __init__(self, provider_uri: str) -> None:
        self._provider_uri = provider_uri

    async def list_factions(self) -> list[str]:
        """Fetch the provider's faction index, degrading to an empty list."""
        from kingdoms.core.rpc.content_client import ContentProviderClient

        return await ContentProviderClient(self._provider_uri).list_factions()

    async def faction_content(self, faction_key: str, locale: str) -> LocalizedContent | None:
        """Fetch one faction's descriptor, degrading to None when unknown."""
        from kingdoms.core.rpc.content_client import ContentProviderClient

        payload = await ContentProviderClient(self._provider_uri).get_faction_content(faction_key, locale)
        if payload is None:
            return None
        return LocalizedContent(
            entity_id=payload["entity_id"],
            locale=payload["locale"],
            name=payload["name"],
            summary=payload["summary"],
            source_url=payload["source_url"],
            image_url=str(payload.get("image_url", "")),
        )

    async def map_content(self, map_key: str, locale: str) -> LocalizedContent | None:
        """Fetch one map's descriptor, degrading to None when unknown."""
        from kingdoms.core.rpc.content_client import ContentProviderClient

        payload = await ContentProviderClient(self._provider_uri).get_map_content(map_key, locale)
        if payload is None:
            return None
        return LocalizedContent(
            entity_id=payload["entity_id"],
            locale=payload["locale"],
            name=payload["name"],
            summary=payload["summary"],
            source_url=payload["source_url"],
            image_url=str(payload.get("image_url", "")),
        )


def resolve_content_source(local: GameContentSource, provider_uri: str) -> GameContentSource:
    """Pick the source for one game (local dataset default, RPC optional).

    A set provider URI routes content through the game's ext provider
    process; an empty URI keeps the local in-process provider. The game
    module owns which env/config feeds ``provider_uri`` — this seam stays
    game-agnostic.
    """
    if provider_uri:
        logger.info("CONTENT SOURCE: rpc provider at %s", provider_uri)
        return RpcContentSource(provider_uri)
    return local
