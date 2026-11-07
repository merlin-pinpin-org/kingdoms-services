"""The critical caller-to-remote seams, end to end over a real wire (#322).

A flow tested with a fake service is not tested: the fake accepts any
signature and never refuses. Each critical seam therefore has ONE test
that crosses the real transports — the real client, the real servicer,
the real wire codecs — over loopback gRPC, with only the backing
callables faked. The catalog seam (GameData.Call) is covered by
``test_catalog_gateway.py``; these are the content seam and the
provider-proxy seam.
"""

from __future__ import annotations

from typing import Any

import grpc
import pytest

from kingdoms.core.models.game import MatchResult, ProviderCapabilities
from kingdoms.core.rpc.content import ContentServicer
from kingdoms.core.rpc.content_client import ContentProviderClient
from kingdoms.core.rpc.game import GameServicer
from kingdoms.core.rpc.game_client import GameProviderClient
from kingdoms.game_aoe2.client import GameDataClient
from kingdoms.game_aoe2.rpc import GameDataServicer
from kingdoms.rpc_generated.kingdoms.v1 import content_pb2_grpc, game_pb2_grpc, gamedata_pb2_grpc

FACTION = {
    "entity_id": "faction:aztecs",
    "locale": "fr",
    "name": "Aztèques",
    "summary": "La civilisation mésoaméricaine",
    "source_url": "https://wiki.example/aztecs",
    "image_url": "https://wiki.example/aztecs.png",
}

SEED = {
    "name": "Arabia",
    "description": "The classic",
    "resource_url": "https://wiki.example/arabia",
    "terrain": "Désert",
    "size": "2-8",
    "map_type": "land",
    "licence_ok": True,
    "game_versions": ["DE"],
    "versions": [{"name": "RM", "description": "Random map", "details": ["1v1"]}],
    "tags": ["classic"],
}


async def _start(server: grpc.aio.Server) -> int:
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return port


@pytest.mark.asyncio
async def test_the_content_seam_round_trips_over_the_real_wire() -> None:
    """ContentProviderClient -> real ContentServicer: the real consumer path."""
    server = grpc.aio.server()
    servicer = ContentServicer(
        provider_key="ext-liquipedia",
        game_key="aoe2",
        locales=("fr", "en"),
        list_factions=_list_factions,
        faction_content=_faction_content,
        map_content=_map_content,
        map_seed=_map_seed,
    )
    content_pb2_grpc.add_ContentServicer_to_server(servicer, server)
    port = await _start(server)
    try:
        client = ContentProviderClient(f"127.0.0.1:{port}")

        keys = await client.list_factions()
        assert keys == ["aztecs", "britons"]

        content = await client.get_faction_content("aztecs", "fr")
        assert content is not None
        assert content["name"] == "Aztèques"
        assert content["entity_id"] == "faction:aztecs"

        # an unknown key degrades to None — the caller falls back, never crashes
        assert await client.get_faction_content("unknown", "fr") is None

        seed = await client.get_map_seed("aoe2", "arabia")
        assert seed is not None
        assert seed["name"] == "Arabia"
        assert seed["versions"][0]["name"] == "RM"

        # an unknown map degrades to None (found=False on the wire)
        assert await client.get_map_seed("aoe2", "unknown") is None
    finally:
        await server.stop(0)


@pytest.mark.asyncio
async def test_the_provider_proxy_seam_round_trips_over_the_real_wire() -> None:
    """GameDataClient -> real GameDataServicer -> real GameProviderClient.

    The full production chain of the bot's provider proxy: the bot's
    client, the game service's servicer, the service's own provider
    client and the provider's servicer — four real pieces over two
    loopback wires, only the provider's backing callables faked.
    """
    provider_server = grpc.aio.server()
    provider_servicer = GameServicer(
        lambda: ProviderCapabilities(
            provider_id="ext-librematch", game_key="aoe2", player_stats=True
        ),
        player_stats=_player_stats,
        match_result=_match_result,
    )
    game_pb2_grpc.add_GameServicer_to_server(provider_servicer, provider_server)
    provider_port = await _start(provider_server)

    game_server = grpc.aio.server()
    game_servicer = GameDataServicer(
        aggregator=None,
        stats_provider=GameProviderClient(
            f"127.0.0.1:{provider_port}", "ext-librematch", "aoe2"
        ),
    )
    gamedata_pb2_grpc.add_GameDataServicer_to_server(game_servicer, game_server)
    game_port = await _start(game_server)
    try:
        bot_client = GameDataClient(f"127.0.0.1:{game_port}")

        caps = await bot_client.get_capabilities()
        assert caps.provider_id == "ext-librematch"
        assert caps.player_stats is True

        stats = await bot_client.get_player_stats("123")
        assert stats is not None
        block = stats.blocks[0]
        assert block.name == "rm_1v1"
        entries = {e.key: e.value for e in block.entries}
        assert entries["rating"] == "1650"

        # a profile without stats degrades to None, never a crash
        assert await bot_client.get_player_stats("unknown") is None

        result = await bot_client.get_match_result(["123"], started_after=0)
        assert result is not None
        assert result.winner_profile_id == "123"
        assert result.map_name == "Arabia"
        assert result.ratings["123"] == (1600, 1650)
    finally:
        await game_server.stop(0)
        await provider_server.stop(0)


async def _list_factions() -> list[str]:
    return ["aztecs", "britons"]


async def _faction_content(faction_key: str, locale: str) -> dict[str, Any] | None:
    if faction_key == "aztecs" and locale == "fr":
        return dict(FACTION)
    return None


async def _map_content(map_key: str, locale: str) -> dict[str, Any] | None:
    return None


async def _map_seed(game_key: str, map_key: str) -> dict[str, Any] | None:
    if map_key == "arabia":
        return dict(SEED)
    return None


async def _player_stats(profile_id: str) -> Any | None:
    from kingdoms.core.models.game import PlayerStats, StatsBlock, StatsEntry

    if profile_id != "123":
        return None
    return PlayerStats(
        profile_id=profile_id,
        blocks=(
            StatsBlock(
                name="rm_1v1",
                entries=(
                    StatsEntry(key="rating", value="1650"),
                    StatsEntry(key="wins", value="42"),
                ),
            ),
        ),
    )


async def _match_result(profile_ids: list[str], started_after: int) -> MatchResult | None:
    return MatchResult(
        match_ref="m1",
        winner_profile_id="123",
        loser_profile_ids=("456",),
        map_name="Arabia",
        duration_ms=900_000,
        ended_at=1_700_000_000,
        factions={"123": "aztecs", "456": "britons"},
        ratings={"123": (1600, 1650)},
    )
