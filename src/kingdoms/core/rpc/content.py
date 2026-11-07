"""Game content RPC seam — server side (kingdoms.v1.Content).

A game content provider process serves localized
game catalog data (factions, maps) through this servicer. The seam keeps
the upstream concern out of the core: it only translates between the
wire contract and the plain content descriptors. The upstream the ext
process reads (vendored dataset, HTTP API, ...) is swappable per
process — the consumers never change.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

from kingdoms.rpc_generated.kingdoms.v1 import content_pb2, content_pb2_grpc

logger = logging.getLogger("kingdoms.core.rpc.content")


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
        map_seed: Callable[[str, str], Awaitable[dict[str, Any] | None]] | None = None,
    ) -> None:
        self._provider_key = provider_key
        self._game_key = game_key
        self._locales = locales
        self._list_factions = list_factions
        self._faction_content = faction_content
        self._map_content = map_content
        self._map_seed = map_seed

    async def GetContentCapabilities(
        self,
        request: content_pb2.ContentCapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.ContentCapabilities:
        """Serve the provider identity and supported locale surface."""
        return content_capabilities_to_wire(self._provider_key, self._game_key, self._locales)

    def _game_served(self, game_key: str, context: grpc.aio.ServicerContext) -> bool:
        """Gate one call on the provider's served games (owner direction 2026-11-06).

        A provider serves a DECLARED list of games (its capabilities);
        a client asking for a game outside the list is a wiring error:
        an explicit log + an INVALID_ARGUMENT abort — never a silent
        wrong-wiki answer.
        """
        if game_key == self._game_key:
            return True
        logger.warning(
            "CONTENT SCOPE: game %r is NOT served by this provider "
            "(serves: %r) — the client's wiring is wrong",
            game_key,
            self._game_key,
        )
        import asyncio

        asyncio.get_running_loop().create_task(
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"this provider serves {self._game_key!r}, not {game_key!r}",
            )
        )
        return False

    async def GetMapSeed(
        self,
        request: content_pb2.MapSeedRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.MapSeedReply:
        """Serve one map's full descriptor (the import flow)."""
        if not self._game_served(request.game_key, context):
            import asyncio

            raise asyncio.CancelledError  # the abort above closes the call
        if self._map_seed is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "map seed not available")
        seed = await self._map_seed(request.game_key, request.map_key)
        if seed is None:
            return content_pb2.MapSeedReply(found=False)
        licence_ok = bool(seed.get("licence_ok", True))
        return content_pb2.MapSeedReply(
            found=True,
            name=str(seed.get("name", "")),
            description=str(seed.get("description", "")),
            resource_url=str(seed.get("resource_url", "")),
            image_url=str(seed.get("image_url", "")),
            icon_url=str(seed.get("icon_url", "")),
            terrain=str(seed.get("terrain", "")),
            size=str(seed.get("size", "")),
            map_type=str(seed.get("map_type", "")),
            licence_ok=licence_ok,
            licence_note=str(seed.get("licence_note", "")),
            walls=bool(seed.get("walls", False)),
            nomad=bool(seed.get("nomad", False)),
            tc=bool(seed.get("tc", False)),
            walls_label=str(seed.get("walls_label", "")),
            nomad_label=str(seed.get("nomad_label", "")),
            tc_label=str(seed.get("tc_label", "")),
            player_capacity=str(seed.get("player_capacity", "")),
            creator=str(seed.get("creator", "")),
            game_versions=[str(v) for v in seed.get("game_versions", ()) or ()],
            versions=[
                content_pb2.MapSeedVersion(
                    name=str(v.get("name", "")),
                    description=str(v.get("description", "")),
                    details=[str(d) for d in v.get("details", ()) or ()],
                )
                for v in seed.get("versions", ()) or ()
            ],
            tags=[str(t) for t in seed.get("tags", ()) or ()],
        )

    async def ListFactions(
        self,
        request: content_pb2.ListFactionsRequest,
        context: grpc.aio.ServicerContext,
    ) -> content_pb2.FactionList:
        """Serve every faction key the upstream source knows."""
        logger.info(
            "CONTENT SERVE: ListFactions provider=%s game=%s",
            self._provider_key,
            self._game_key,
        )
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
        """Serve one map's localized content, NOT_FOUND when unknown.

        ``MapContentRequest`` carries no game_key (the contract): the provider
        serves its single declared game by construction, so the game gate
        runs only where the wire message carries the game (``GetMapSeed``).
        """
        content = await self._map_content(request.map_key, request.locale)
        if content is None or not content.get("found", True):
            await context.abort(grpc.StatusCode.NOT_FOUND, "unknown map_key")
        return localized_content_to_wire(content)


def add_content_servicer(server: grpc.aio.Server, servicer: ContentServicer) -> None:
    """Bind the Content servicer onto the server."""
    content_pb2_grpc.add_ContentServicer_to_server(servicer, server)
