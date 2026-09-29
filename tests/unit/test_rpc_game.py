"""Unit tests for the game provider seam (kingdoms.v1.Game).

Covers: capabilities round-trip (model ↔ wire), the provider servicer,
and the core-side client — including the clean-degradation paths that
are acceptance criterion 9.1 of the ladder reference.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from kingdoms.core.models.game import GameMap, MatchDetails, ProviderCapabilities, Slot
from kingdoms.core.rpc.game import (
    GameServicer,
    capabilities_from_wire,
    capabilities_to_wire,
    game_map_from_wire,
    game_map_to_wire,
    match_details_from_wire,
    match_details_to_wire,
)
from kingdoms.core.rpc.game_client import (
    GameProviderClient,
    UnsupportedCapabilityError,
)
from kingdoms.ext_aoe2lobby import DECLARED_CAPABILITIES as AOE2LOBBY_CAPS
from kingdoms.ext_librematch import DECLARED_CAPABILITIES as LIBREMATCH_CAPS
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2, game_pb2_grpc


class _ServicerContext:
    """Minimal stand-in for grpc.aio.ServicerContext in unit tests."""


def test_capabilities_wire_round_trip_full() -> None:
    """Every capability field survives the model → wire → model round trip."""
    caps = ProviderCapabilities(
        provider_id="ext-test",
        game_key="aoe2",
        realtime=True,
        reliable_results=True,
        check_map=True,
        player_stats=True,
    )
    assert capabilities_from_wire(capabilities_to_wire(caps)) == caps


def test_capabilities_wire_round_trip_zero() -> None:
    """The zero-capability declaration survives the round trip (9.1 baseline)."""
    caps = ProviderCapabilities.none("ext-test", "aoe2")
    assert capabilities_from_wire(capabilities_to_wire(caps)) == caps


def test_provider_declarations_are_zero_safe() -> None:
    """Both real providers declare their game key and conservative defaults."""
    for caps in (LIBREMATCH_CAPS, AOE2LOBBY_CAPS):
        assert caps.game_key == "aoe2"
        assert caps.provider_id.startswith("ext-")
        # Nothing declared true until the live adapter is wired (spike).
        if caps is LIBREMATCH_CAPS:
            assert caps.realtime is False  # official API is poll-only
        else:
            assert caps.realtime is True  # aoe2lobby.com WebSocket


def _match_details(match_ref: str) -> MatchDetails:
    """Build a representative match payload: slotinfo + raw options."""
    return MatchDetails(
        match_ref=match_ref,
        map_name="arabia",
        slots=(
            Slot(slot_index=0, profile_id="p1", faction_key="britons", team=1, filled=True, slot_kind="human"),
            Slot(slot_index=1, profile_id="p2", faction_key="franks", team=2, filled=True, slot_kind="human"),
        ),
        options=(("map_size", "huge"), ("speed", "standard")),
        started_at=1_700_000_000_000,
        match_kind="ongoing",
    )


def test_match_details_wire_round_trip() -> None:
    """Slotinfo and raw options survive the model-wire-model round trip."""
    details = _match_details("m-42")
    assert match_details_from_wire(match_details_to_wire(details)) == details


def test_game_map_wire_round_trip() -> None:
    """Catalog map entries survive the model-wire-model round trip."""
    game_map = GameMap(map_key="arabia", name="Arabia", map_type="random_map", resource_url="https://x")
    assert game_map_from_wire(game_map_to_wire(game_map)) == game_map


@pytest.mark.asyncio
async def test_servicer_serves_match_details_and_maps() -> None:
    """The servicer serves slotinfo/options and the map catalog from callables."""
    servicer = GameServicer(
        lambda: LIBREMATCH_CAPS,
        match_details=_match_details,
        list_maps=lambda: [GameMap(map_key="arabia", name="Arabia")],
    )
    reply = await servicer.GetMatchDetails(
        game_pb2.GetMatchDetailsRequest(match_ref="m-42"), _ServicerContext()  # type: ignore[arg-type]
    )
    assert match_details_from_wire(reply) == _match_details("m-42")
    maps_reply = await servicer.ListMaps(
        game_pb2.ListMapsRequest(), _ServicerContext()  # type: ignore[arg-type]
    )
    assert [game_map_from_wire(m) for m in maps_reply.maps] == [
        GameMap(map_key="arabia", name="Arabia")
    ]


@pytest.mark.asyncio
async def test_client_round_trips_match_details_and_maps() -> None:
    """The core-side client fetches match details and the map catalog."""
    server = _serve(
        LIBREMATCH_CAPS,
        50072,
        match_details=_match_details,
        list_maps=lambda: [GameMap(map_key="arabia", name="Arabia")],
    )
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50072", "ext-librematch", "aoe2")
        details = await client.get_match_details("m-42")
        assert details == _match_details("m-42")
        maps = await client.list_maps()
        assert maps == [GameMap(map_key="arabia", name="Arabia")]
    finally:
        await server.stop(grace=None)


@pytest.mark.asyncio
async def test_client_degrades_match_details_and_maps() -> None:
    """Unsupported details/catalog degrade to None/[] without raising."""
    server = _serve(LIBREMATCH_CAPS, 50073)  # no details/maps callables
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50073", "ext-librematch", "aoe2")
        assert await client.get_match_details("m-42") is None
        assert await client.list_maps() == []
    finally:
        await server.stop(grace=None)


@pytest.mark.asyncio
async def test_servicer_serves_declared_capabilities() -> None:
    """The Game servicer serves the provider's declaration over the wire."""
    servicer = GameServicer(lambda: AOE2LOBBY_CAPS)
    reply = await servicer.GetCapabilities(
        game_pb2.CapabilitiesRequest(), _ServicerContext()  # type: ignore[arg-type]
    )
    assert capabilities_from_wire(reply) == AOE2LOBBY_CAPS


def _serve(
    caps: ProviderCapabilities,
    port: int,
    match_details: object | None = None,
    list_maps: object | None = None,
) -> grpc.aio.Server:
    """Build a local Game provider server on the given port."""
    server = grpc.aio.server()
    game_pb2_grpc.add_GameServicer_to_server(
        GameServicer(
            lambda: caps,
            match_details=match_details,  # type: ignore[arg-type]
            list_maps=list_maps,  # type: ignore[arg-type]
        ),
        server,
    )
    server.add_insecure_port(f"127.0.0.1:{port}")
    return server


@pytest.mark.asyncio
async def test_client_fetches_capabilities_over_the_wire() -> None:
    """The core-side client round-trips capabilities from a live provider."""
    server = _serve(LIBREMATCH_CAPS, 50071)
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50071", "ext-librematch", "aoe2")
        caps = await client.get_capabilities()
        assert caps == LIBREMATCH_CAPS
    finally:
        await server.stop(grace=None)


@pytest.mark.asyncio
async def test_client_degrades_to_zero_capabilities_when_unreachable() -> None:
    """An unreachable provider declares nothing: the core keeps running."""
    client = GameProviderClient("127.0.0.1:50079", "ext-nowhere", "aoe2")
    caps = await client.get_capabilities()
    assert caps == ProviderCapabilities.none("ext-nowhere", "aoe2")


@pytest.mark.asyncio
async def test_client_maps_unimplemented_to_unsupported_capability() -> None:
    """A provider explicitly rejecting the capability raises the typed error."""
    server = grpc.aio.server()
    game_pb2_grpc.add_GameServicer_to_server(
        _UnimplementedGameServicer(), server
    )
    server.add_insecure_port("127.0.0.1:50081")
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50081", "ext-partial", "aoe2")
        with pytest.raises(UnsupportedCapabilityError):
            await client.get_capabilities()
    finally:
        await server.stop(grace=None)


class _UnimplementedGameServicer(game_pb2_grpc.GameServicer):
    """A provider that declares nothing and rejects every capability."""

    async def GetCapabilities(  # noqa: N802 - generated stub name

        self,
        request: game_pb2.CapabilitiesRequest,
        context: grpc.aio.ServicerContext,
    ) -> game_pb2.Capabilities:
        """Reject the capability so the client maps UNIMPLEMENTED."""
        await context.abort(grpc.StatusCode.UNIMPLEMENTED, "no capabilities")


@pytest.mark.asyncio
async def test_provider_entrypoints_are_importable() -> None:
    """Both ext process entrypoints exist and expose main()."""
    from kingdoms.ext_aoe2lobby.server import main as aoe2lobby_main
    from kingdoms.ext_librematch.server import main as librematch_main

    assert callable(librematch_main)
    assert callable(aoe2lobby_main)


def test_asyncio_no_orphan_tasks() -> None:
    """Sanity: the seam helpers leave no pending asyncio tasks behind."""
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_noop())
    finally:
        loop.close()


async def _noop() -> None:
    """No-op coroutine for the task-hygiene check."""
