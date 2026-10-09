"""Game content RPC seam — server side (kingdoms.v1.Content).

A game content provider process (ext-aoe2techtree, ...) serves localized
game catalog data (factions, maps) through this servicer. The seam keeps
the upstream concern out of the core: it only translates between the
wire contract and the plain content descriptors. The upstream the ext
process reads (vendored dataset, HTTP API, ...) is swappable per
process — the consumers never change.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import grpc

from kingdoms.rpc_generated.kingdoms.v1 import content_pb2, content_pb2_grpc


def content_capabilities_to_wire(
    provider_key: str,
    game_key: str,
    locales: tuple[str, ...],
) -> content_pb2.ContentCapabilities:
    """Translate a content capability declaration into the wire message."""
    return content_pb2.ContentCapabilities(
        provider_key=provider_key,
        game_key=game_key,
        locales=list(locales),
    )


def content_capabilities_from_wire(
    wire: content_pb2.ContentCapabilities,
) -> tuple[str, str, tuple[str, ...]]:
    """Translate a wire capability message into its plain parts."""
    return wire.provider_key, wire.game_key, tuple(wire.locales)


def localized_content_to_wire(content: dict[str, str | bool]) -> content_pb2.LocalizedContent:
    """Translate a plain content descriptor into the wire message."""
    return content_pb2.LocalizedContent(
        entity_id=str(content.get("entity_id", "")),
        locale=str(content.get("locale", "")),
        name=str(content.get("name", "")),
        summary=str(content.get("summary", "")),
        source_url=str(content.get("source_url", "")),
        found=bool(content.get("found", True)),
        image_url=str(content.get("image_url", "")),
    )


def localized_content_from_wire(wire: content_pb2.LocalizedContent) -> dict[str, str | bool]:
    """Translate a wire content message into a plain descriptor."""
    return {
        "entity_id": wire.entity_id,
        "locale": wire.locale,
        "name": wire.name,
        "summary": wire.summary,
        "source_url": wire.source_url,
        "found": wire.found,
        "image_url": wire.image_url,
    }


class ContentServicer(content_pb2_grpc.ContentServicer):
    """Serve the kingdoms.v1.Content contract from source-backed callables."""

    def __init__(
        self,
        provider_key: str,
        game_key: str,
        locales: tuple[str, ...],
        list_factions: Callable[[], Awaitable[list[str]]],
        faction_content: Callable[[str, str], Awaitable[dict[str, str | bool] | None]],
        map_content: Callable[[str, str], Awaitable[dict[str, str | bool] | None]],
    ) -> None:
        self._provider_key = provider_key
        self._game_key = game_key
        self._locales = locales
        self._list_factions = list_factions
        self._faction_content = faction_content
        self._map_content = map_content

    async def GetContentCapabilities(
        self,
        request: content_pb2.ContentCapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.ContentCapabilities:
        """Serve the provider identity and supported locale surface."""
        return content_capabilities_to_wire(self._provider_key, self._game_key, self._locales)

    async def ListFactions(
        self,
        request: content_pb2.ListFactionsRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.FactionList:
        """Serve every faction key the upstream source knows."""
        keys = await self._list_factions()
        return content_pb2.FactionList(faction_keys=keys)

    async def GetFactionContent(
        self,
        request: content_pb2.FactionContentRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.LocalizedContent:
        """Serve one faction's localized content, NOT_FOUND when unknown."""
        content = await self._faction_content(request.faction_key, request.locale)
        if content is None or not content.get("found", True):
            await context.abort(grpc.StatusCode.NOT_FOUND, "unknown faction_key")
        return localized_content_to_wire(content)

    async def GetMapContent(
        self,
        request: content_pb2.MapContentRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.LocalizedContent:
        """Serve one map's localized content, NOT_FOUND when unknown."""
        content = await self._map_content(request.map_key, request.locale)
        if content is None or not content.get("found", True):
            await context.abort(grpc.StatusCode.NOT_FOUND, "unknown map_key")
        return localized_content_to_wire(content)


def add_content_servicer(server: grpc.aio.Server, servicer: ContentServicer) -> None:
    """Bind the Content servicer onto the server."""
    content_pb2_grpc.add_ContentServicer_to_server(servicer, server)
