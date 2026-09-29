"""Game provider RPC seam — server side (kingdoms.v1.Game).

A game provider process (ext-librematch, ext-aoe2lobby) builds its gRPC
server from these servicers, backed by its own adapters to the external
APIs. The seam keeps provider code out of the core: it only translates
between the wire contract and plain core models.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import grpc

from kingdoms.core.models.game import (
    GameMap,
    MatchDetails,
    ProviderCapabilities,
    Slot,
)
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2, game_pb2_grpc


def capabilities_to_wire(caps: ProviderCapabilities) -> game_pb2.Capabilities:
    """Translate a core capability declaration into the wire message."""
    return game_pb2.Capabilities(
        provider_id=caps.provider_id,
        game_key=caps.game_key,
        realtime=caps.realtime,
        reliable_results=caps.reliable_results,
        check_map=caps.check_map,
        player_stats=caps.player_stats,
    )


def capabilities_from_wire(wire: game_pb2.Capabilities) -> ProviderCapabilities:
    """Translate a wire capability message into the core model."""
    return ProviderCapabilities(
        provider_id=wire.provider_id,
        game_key=wire.game_key,
        realtime=wire.realtime,
        reliable_results=wire.reliable_results,
        check_map=wire.check_map,
        player_stats=wire.player_stats,
    )


def slot_to_wire(slot: Slot) -> game_pb2.Slot:
    """Translate a core slot model into the wire message."""
    return game_pb2.Slot(
        slot_index=slot.slot_index,
        profile_id=slot.profile_id,
        faction_key=slot.faction_key,
        team=slot.team,
        filled=slot.filled,
        slot_kind=slot.slot_kind,
    )


def slot_from_wire(wire: game_pb2.Slot) -> Slot:
    """Translate a wire slot message into the core model."""
    return Slot(
        slot_index=wire.slot_index,
        profile_id=wire.profile_id,
        faction_key=wire.faction_key,
        team=wire.team,
        filled=wire.filled,
        slot_kind=wire.slot_kind,
    )


def match_details_to_wire(details: MatchDetails) -> game_pb2.MatchDetails:
    """Translate a core match-details model into the wire message."""
    return game_pb2.MatchDetails(
        match_ref=details.match_ref,
        map_name=details.map_name,
        slots=[slot_to_wire(s) for s in details.slots],
        options=dict(details.options),
        started_at=details.started_at,
        match_kind=details.match_kind,
    )


def match_details_from_wire(wire: game_pb2.MatchDetails) -> MatchDetails:
    """Translate a wire match-details message into the core model."""
    return MatchDetails(
        match_ref=wire.match_ref,
        map_name=wire.map_name,
        slots=tuple(slot_from_wire(s) for s in wire.slots),
        options=tuple(sorted(wire.options.items())),
        started_at=wire.started_at,
        match_kind=wire.match_kind,
    )


def game_map_to_wire(game_map: GameMap) -> game_pb2.GameMap:
    """Translate a core game-map model into the wire message."""
    return game_pb2.GameMap(
        map_key=game_map.map_key,
        name=game_map.name,
        map_type=game_map.map_type,
        resource_url=game_map.resource_url,
    )


def game_map_from_wire(wire: game_pb2.GameMap) -> GameMap:
    """Translate a wire game-map message into the core model."""
    return GameMap(
        map_key=wire.map_key,
        name=wire.name,
        map_type=wire.map_type,
        resource_url=wire.resource_url,
    )


class GameServicer(game_pb2_grpc.GameServicer):
    """Serve the kingdoms.v1.Game contract from provider-backed callables.

    Providers register plain async callables; this servicer adapts them to
    gRPC. Providers that do not support a capability raise
    ``UnsupportedCapability`` in the backing callable, mapped to
    UNIMPLEMENTED on the wire so the core degrades cleanly.
    """

    def __init__(
        self,
        capabilities: Callable[[], ProviderCapabilities],
        match_details: Callable[[str], Awaitable[MatchDetails | None]] | None = None,
        list_maps: Callable[[], Awaitable[list[GameMap]]] | None = None,
    ) -> None:
        self._capabilities = capabilities
        self._match_details = match_details
        self._list_maps = list_maps

    async def GetCapabilities(
        self,
        request: game_pb2.CapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> game_pb2.Capabilities:
        """Serve the provider's declared capabilities as the wire message."""
        return capabilities_to_wire(self._capabilities())

    async def GetMatchDetails(
        self,
        request: game_pb2.GetMatchDetailsRequest,
        context: grpc.aio.ServicerContext,
    ) -> game_pb2.MatchDetails:
        """Serve a match's slotinfo and raw game options when known."""
        if self._match_details is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "match details not available")
        details = await self._match_details(request.match_ref)
        if details is None:
            await context.abort(grpc.StatusCode.NOT_FOUND, "unknown match_ref")
        return match_details_to_wire(details)

    async def ListMaps(
        self,
        request: game_pb2.ListMapsRequest,
        context: grpc.aio.ServicerContext,
    ) -> game_pb2.GameMaps:
        """Serve the provider's known map catalog for its game."""
        if self._list_maps is None:
            await context.abort(grpc.StatusCode.UNIMPLEMENTED, "map catalog not available")
        maps = await self._list_maps()
        return game_pb2.GameMaps(maps=[game_map_to_wire(m) for m in maps])
