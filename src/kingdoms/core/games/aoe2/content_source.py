"""Abstract game content source for the core (consumer-side seam).

The core never reads a content upstream directly: it goes through this
seam. Two implementations ship:

- ``LocalDatasetSource`` — the vendored aoe2techtree dataset read
  in-process (default, current behavior of the DM refresh button and
  the ``/seed <env> game-content`` CLI).
- ``RpcContentSource`` — the ext-aoe2techtree provider process over
  gRPC (``EXT_AOE2TECHTREE_URI``), which itself may read the dataset or
  a replacement HTTP API.

Both speak the same interface, so switching to another API upstream is a
deployment concern (run the ext process, point the env var), never a
core code change.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from kingdoms.core.games.aoe2.faction_content import LocalizedContent

logger = logging.getLogger("kingdoms.core.content_source")


class GameContentSource(Protocol):
    """Localized game content served by an upstream (dataset or API)."""

    async def list_factions(self) -> list[str]:
        """List every faction key known to the source."""

    async def faction_content(
        self, faction_key: str, locale: str
    ) -> LocalizedContent | None:
        """Return one faction's localized descriptor, None when unknown."""


class LocalDatasetSource:
    """Source = the vendored dataset, read in-process (current behavior)."""

    def __init__(self, provider: object) -> None:
        self._provider = provider

    async def list_factions(self) -> list[str]:
        """List the dataset's civ keys."""
        return list(self._provider.faction_names())  # type: ignore[attr-defined]

    async def faction_content(self, faction_key: str, locale: str) -> LocalizedContent | None:
        """Extract the faction's descriptor from the vendored dataset."""
        result: LocalizedContent | None = self._provider.faction_content(  # type: ignore[attr-defined]
            faction_key, locale
        )
        return result


class RpcContentSource:
    """Source = the ext-aoe2techtree process over gRPC (kingdoms.v1.Content)."""

    def __init__(self, provider_uri: str) -> None:
        self._provider_uri = provider_uri

    async def list_factions(self) -> list[str]:
        """Fetch the provider's faction index, degrading to an empty list."""
        from kingdoms.core.rpc.content_client import ContentProviderClient

        return await ContentProviderClient(self._provider_uri).list_factions()

    async def faction_content(self, faction_key: str, locale: str) -> LocalizedContent | None:
        """Fetch one faction's descriptor, degrading to None when unknown."""
        from kingdoms.core.rpc.content_client import ContentProviderClient

        payload = await ContentProviderClient(self._provider_uri).get_faction_content(
            faction_key, locale
        )
        if payload is None:
            return None
        return LocalizedContent(
            entity_id=str(payload["entity_id"]),
            locale=str(payload["locale"]),
            name=str(payload["name"]),
            summary=str(payload["summary"]),
            source_url=str(payload["source_url"]),
        )


def resolve_content_source(
    provider: object,
    env: dict[str, str] | None = None,
) -> GameContentSource:
    """Pick the content source from the environment (dataset default).

    ``EXT_AOE2TECHTREE_URI`` set → the ext-aoe2techtree gRPC process;
    unset → the vendored dataset read in-process. The provider object is
    the local ``TechtreeContentProvider``, kept as the default so the
    refresh button and the CLI keep working unchanged.
    """
    lookup = env if env is not None else dict(os.environ)
    uri = lookup.get("EXT_AOE2TECHTREE_URI", "")
    if uri:
        logger.info("CONTENT SOURCE: rpc provider at %s", uri)
        return RpcContentSource(uri)
    return LocalDatasetSource(provider)
