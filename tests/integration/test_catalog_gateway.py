"""End-to-end: the catalog gateway — the game service owns the data.

A real gRPC round-trip: the GameDataServicer (with a real GameDataService
over a fake database) answers a RemoteGameDataService call; the models
cross the wire and come back as the same pydantic shapes; a functional
error (a taken name) re-raises as the SAME exception type.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeCollection:
    """One collection's documents (the fake's storage)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}


class _FakeDB:
    """The DB-level shape GameDataService expects (collection + payload)."""

    def __init__(self) -> None:
        self._collections: dict[str, _FakeCollection] = {}

    def _col(self, collection: str) -> _FakeCollection:
        return self._collections.setdefault(collection, _FakeCollection())

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self._col(collection).docs[str(document["_id"])] = dict(document)

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self._col(collection).docs.get(entry_id)

    async def find_by_name(
        self, collection: str, game_key: str, name: str
    ) -> dict[str, Any] | None:
        for doc in self._col(collection).docs.values():
            if doc.get("game_key") == game_key and doc.get("name") == name:
                return doc
        return None

    async def find_active_maps(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [d for d in self._col("maps").docs.values() if d.get("game_key") == game_key]

    async def find_active_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def find_all_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def find_active_factions(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return []

    async def delete_entry(self, collection: str, entry_id: str) -> bool:
        return self._col(collection).docs.pop(entry_id, None) is not None

    async def find_game_keys(self) -> list[str]:
        keys: set[str] = set()
        for col in self._collections.values():
            keys.update(str(d.get("game_key")) for d in col.docs.values())
        return sorted(keys)


@pytest.mark.asyncio
async def test_catalog_gateway_round_trip() -> None:
    """Create → list → get over the real wire; the models survive."""
    import grpc

    from kingdoms.core.services.game_data import GameDataService, NameTakenError
    from kingdoms.game_aoe2.remote_catalog import RemoteGameDataService
    from kingdoms.game_aoe2.rpc import GameDataServicer
    from kingdoms.rpc_generated.kingdoms.v1 import gamedata_pb2_grpc

    database = _FakeDB()
    servicer = GameDataServicer(
        aggregator=None,
        catalog=GameDataService(database),  # type: ignore[arg-type]
    )
    server = grpc.aio.server()
    gamedata_pb2_grpc.add_GameDataServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        remote = RemoteGameDataService(f"127.0.0.1:{port}")

        # create over the wire
        created = await remote.create_map(
            "aoe2", "Arabia", filename="arabia", description="The classic"
        )
        assert created.name == "Arabia"
        assert created.id.startswith("map:")

        # list + get rebuild the same model shapes
        maps = await remote.list_maps("aoe2")
        assert [m.name for m in maps] == ["Arabia"]
        fetched = await remote.get_map(created.id)
        assert fetched is not None and fetched.description == "The classic"

        # a taken name re-raises the SAME exception type
        with pytest.raises(NameTakenError):
            await remote.create_map("aoe2", "Arabia", filename="arabia")

        # delete over the wire
        assert await remote.delete_map(created.id) is True
        assert await remote.get_map(created.id) is None
    finally:
        await server.stop(0)
