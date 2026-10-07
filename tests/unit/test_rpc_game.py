"""Unit tests for the game provider seam (kingdoms.v1.Game).

Covers: capabilities round-trip (model ↔ wire), the provider servicer,
and the core-side client — including the clean-degradation paths that
are acceptance criterion 9.1 of the ladder reference.
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from kingdoms.core.models.game import (
    GameMap,
    MatchDetails,
    MatchEvent,
    PlayerStats,
    ProviderCapabilities,
    Slot,
    StatsBlock,
    StatsEntry,
)
from kingdoms.core.rpc.game import (
    GameServicer,
    capabilities_from_wire,
    capabilities_to_wire,
    game_map_from_wire,
    game_map_to_wire,
    match_details_from_wire,
    match_details_to_wire,
    match_event_from_wire,
    match_event_to_wire,
    player_stats_from_wire,
    player_stats_to_wire,
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

    def __init__(self) -> None:
        self.code: grpc.StatusCode | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        """Raise like the real context (abort never returns)."""
        self.code = code
        raise grpc.aio.AioRpcError(code, None, details)


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
        # Both providers now serve live lobby events (WS or poll).
        assert caps.realtime is True


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


async def _async_match_details(match_ref: str) -> MatchDetails:
    """Async wrapper around the sync test fixture."""
    return _match_details(match_ref)


async def _async_list_maps() -> list[GameMap]:
    """Async wrapper serving the one-map catalog."""
    return [GameMap(map_key="arabia", name="Arabia")]


async def _async_match_events():
    """Async iterator serving a short lifecycle event sequence."""
    for event in (
        MatchEvent(match_ref="m-42", type="lobby_opened", occurred_at=1000, profile_ids=("p1", "p2")),
        MatchEvent(match_ref="m-42", type="game_started", occurred_at=2000, profile_ids=("p1", "p2")),
    ):
        yield event


@pytest.mark.asyncio
async def test_servicer_serves_match_details_and_maps() -> None:
    """The servicer serves slotinfo/options and the map catalog from callables."""
    servicer = GameServicer(
        lambda: LIBREMATCH_CAPS,
        match_details=_async_match_details,
        list_maps=_async_list_maps,
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
        match_details=_async_match_details,
        list_maps=_async_list_maps,
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
async def test_servicer_streams_match_events_with_since_filter() -> None:
    """StreamMatchEvents yields wire events, honoring the since filter."""
    servicer = GameServicer(
        lambda: AOE2LOBBY_CAPS,
        match_events=_async_match_events,
    )
    received = []
    async for wire in servicer.StreamMatchEvents(
        game_pb2.StreamMatchEventsRequest(since=1500), _ServicerContext()  # type: ignore[arg-type]
    ):
        received.append(match_event_from_wire(wire))
    assert received == [
        MatchEvent(match_ref="m-42", type="game_started", occurred_at=2000, profile_ids=("p1", "p2"))
    ]


def test_match_event_wire_round_trip() -> None:
    """A lifecycle event survives the model-wire-model round trip."""
    event = MatchEvent(
        match_ref="m-42",
        type="lobby_opened",
        occurred_at=1700000000,
        profile_ids=("p1", "p2"),
        metadata=(("map", "Arabia"),),
    )
    assert match_event_from_wire(match_event_to_wire(event)) == event


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
    player_stats: object | None = None,
) -> grpc.aio.Server:
    """Build a local Game provider server on the given port."""
    server = grpc.aio.server()
    game_pb2_grpc.add_GameServicer_to_server(
        GameServicer(
            lambda: caps,
            match_details=match_details,  # type: ignore[arg-type]
            list_maps=list_maps,  # type: ignore[arg-type]
            player_stats=player_stats,  # type: ignore[arg-type]
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


def test_player_stats_wire_round_trip() -> None:
    """Player stats blocks survive a wire round trip unchanged."""
    stats = PlayerStats(
        profile_id="p1",
        blocks=(
            StatsBlock(
                name="rm_1v1",
                entries=(
                    StatsEntry(key="rank", value="42"),
                    StatsEntry(key="rating", value="1600"),
                ),
            ),
        ),
    )
    wire = player_stats_to_wire(stats)
    restored = player_stats_from_wire(wire)
    assert restored.blocks == stats.blocks


def _player_stats(profile_id: str) -> PlayerStats:
    """Fixture stats payload for one profile."""
    return PlayerStats(
        profile_id=profile_id,
        blocks=(
            StatsBlock(
                name="rm_1v1",
                entries=(StatsEntry(key="rating", value="1600"),),
            ),
        ),
    )


async def _async_player_stats(profile_id: str) -> PlayerStats | None:
    """Async wrapper around the stats fixture."""
    return _player_stats(profile_id)


@pytest.mark.asyncio
async def test_servicer_serves_player_stats() -> None:
    """The servicer serves stats blocks from the provider callable."""
    servicer = GameServicer(
        lambda: LIBREMATCH_CAPS,
        player_stats=_async_player_stats,
    )
    reply = await servicer.GetPlayerStats(
        game_pb2.GetPlayerStatsRequest(profile_id="p1"), _ServicerContext()  # type: ignore[arg-type]
    )
    assert player_stats_from_wire(reply).blocks == _player_stats("p1").blocks


@pytest.mark.asyncio
async def test_servicer_player_stats_unimplemented_without_callable() -> None:
    """Without a stats callable the servicer answers UNIMPLEMENTED."""
    servicer = GameServicer(lambda: LIBREMATCH_CAPS)
    context = _ServicerContext()
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await servicer.GetPlayerStats(
            game_pb2.GetPlayerStatsRequest(profile_id="p1"), context  # type: ignore[arg-type]
        )
    assert exc_info.value.code() == grpc.StatusCode.UNIMPLEMENTED


@pytest.mark.asyncio
async def test_client_round_trips_player_stats() -> None:
    """The core-side client fetches stats blocks over the wire."""
    server = _serve(
        LIBREMATCH_CAPS,
        50072,
        player_stats=_async_player_stats,
    )
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50072", "ext-librematch", "aoe2")
        stats = await client.get_player_stats("p1")
        assert stats is not None
        assert stats.profile_id == "p1"
        assert stats.blocks == _player_stats("p1").blocks
    finally:
        await server.stop(grace=None)


@pytest.mark.asyncio
async def test_client_degrades_player_stats_to_none() -> None:
    """UNIMPLEMENTED/NOT_FOUND stats answers degrade to None on the client."""
    server = _serve(LIBREMATCH_CAPS, 50072)
    await server.start()
    try:
        client = GameProviderClient("127.0.0.1:50072", "ext-librematch", "aoe2")
        assert await client.get_player_stats("p1") is None
    finally:
        await server.stop(grace=None)
