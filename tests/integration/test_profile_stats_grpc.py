"""Integration test: real gRPC provider → stats cache path (#147).

Starts a real in-process gRPC server (the true GameServicer with the
ext-librematch capabilities and a canned player_stats callable — the
same wiring ext_server.serve_game_provider performs) and drives it with
the real GameProviderClient over localhost. The cache service is then
exercised end to end: provider fetch, Mongo + Redis writes, Redis hit.
Redis and Mongo are in-memory fakes (no service instances in CI); the
gRPC leg — transport, wire models, retry client, servicer — is real.
"""

from __future__ import annotations

import asyncio
from typing import Any

import grpc

from kingdoms.core.models.game import PlayerStats, ProviderCapabilities, StatsBlock, StatsEntry
from kingdoms.core.rpc.game import GameServicer
from kingdoms.core.rpc.game_client import GameProviderClient
from kingdoms.core.services.profile_stats import PROFILE_STATS_COLLECTION, ProfileStatsService, stats_entry
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2_grpc

STATS = PlayerStats(
    profile_id="123",
    blocks=(
        StatsBlock(
            name="rm_1v1",
            entries=(
                StatsEntry(key="rank", value="17"),
                StatsEntry(key="rating", value="1650"),
                StatsEntry(key="wins", value="42"),
                StatsEntry(key="losses", value="17"),
            ),
        ),
    ),
)


class FakeRedis:
    """In-memory redis-py stand-in (get/set with ex)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int = 0) -> None:
        self.store[key] = value


class FakeCollection:
    """In-memory Mongo collection stand-in (find_one/replace_one)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return self.docs.get(str(query.get("_id", "")))

    async def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        self.docs[str(query.get("_id", ""))] = doc


class FakeDb:
    """database[collection] stand-in."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


async def test_real_grpc_provider_feeds_the_stats_cache(tmp_path: Any) -> None:
    server = grpc.aio.server()
    servicer = GameServicer(
        lambda: ProviderCapabilities(
            provider_id="ext-librematch", game_key="aoe2", player_stats=True
        ),
        player_stats=lambda profile_id: _stats(profile_id),
    )
    game_pb2_grpc.add_GameServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        client = GameProviderClient(f"127.0.0.1:{port}", "ext-librematch", "aoe2")
        fetched = await client.get_player_stats("123")
        assert fetched is not None
        assert stats_entry({"blocks": _blocks(fetched)}, "rm_1v1", "rating") == "1650"

        redis = FakeRedis()
        db = FakeDb()
        service = ProfileStatsService(client, db, redis)
        stats = await service.get("123")
        assert stats is not None
        assert stats_entry(stats, "rm_1v1", "rating") == "1650"
        assert stats_entry(stats, "rm_1v1", "wins") == "42"
        assert "stats:aoe2:123" in redis.store
        assert "aoe2:123" in db.collections[PROFILE_STATS_COLLECTION].docs

        cached = await service.get("123")
        assert cached == stats
    finally:
        await server.stop(None)


async def _stats(profile_id: str) -> PlayerStats:
    """Serve the canned stats after one async beat (the servicer awaits)."""
    await asyncio.sleep(0)
    return STATS


def _blocks(stats: PlayerStats) -> list[dict[str, Any]]:
    return [
        {"name": b.name, "entries": [{"key": e.key, "value": e.value} for e in b.entries]}
        for b in stats.blocks
    ]
