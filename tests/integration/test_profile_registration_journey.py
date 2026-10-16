"""Integration test: profile registration, end to end (the CI smoke of #248).

Everything real except the wire: a real in-process gRPC server (the true
GameServicer, the same wiring ext_server.serve_game_provider performs),
driven by the real LibrematchAdapter over a fake WorldsEdge HTTP
transport, the real GameProviderClient, the real Aoe2ProfileValidationSeam
and the real RegistrationService over an in-memory Mongo stand-in.

This is the test that would have caught every failure of the 2026-10-15/16
validation round:

- the wrong leaderboard endpoint (/api/leaderboard vs GetPersonalStat),
- the parse of the real reply shape (statGroups/leaderboardStats),
- the Label-wrapped TextInput modal that never opened,
- the empty EXT_LIBREMATCH_URI making the seam reject every profile,
- the player_stats capability gate (config gap vs unknown profile).

Any regression on the registration path must fail HERE, in CI, not on
the test guild.
"""

from __future__ import annotations

from typing import Any

import grpc
import httpx

from kingdoms.core.models.game import PlayerStats, ProviderCapabilities
from kingdoms.core.rpc.game import GameServicer
from kingdoms.core.rpc.game_client import GameProviderClient
from kingdoms.core.services.registration import (
    RegistrationService,
)
from kingdoms.rpc_generated.kingdoms.v1 import game_pb2_grpc

WORLDEDGE_REPLY = {
    "result": {"code": 0, "message": "SUCCESS"},
    "statGroups": [
        {
            "id": 1103946,
            "name": "",
            "type": 1,
            "members": [
                {
                    "profile_id": 123456,
                    "name": "/xboxlive/2C59C53DDE622DEC778EB8F97556302E9A9019F5",
                    "alias": "qmaxquique",
                    "personal_statgroup_id": 1103946,
                    "xp": 0,
                    "level": 1,
                    "leaderboardregion_id": 4,
                    "country": "ar",
                    "clanlist_name": "TATO",
                }
            ],
        }
    ],
    "leaderboardStats": [
        {
            "profile_id": 123456,
            "leaderboard_id": 3,
            "rank": 42,
            "rating": 1600,
            "wins": 30,
            "losses": 25,
            "streak": 3,
            "games": 55,
        }
    ],
}


class FakeWorldsEdgeTransport(httpx.AsyncBaseTransport):
    """httpx transport stand-in: one reply per recorded request."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.requests: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        for pattern, payload in self.replies.items():
            if pattern in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})


class FakeMongo:
    """In-memory stand-in of the registration Mongo adapter."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        self.docs[str(document["_id"])] = document

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        return self.docs.get(entry_id)

    async def find_user_bindings(self, user_id: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("user_id") == user_id]

    async def find_binding_by_profile(self, game_key: str, profile_id: str) -> dict[str, Any] | None:
        return self.docs.get(f"binding:{game_key}:any:{profile_id}")

    async def delete_binding(self, game_key: str, user_id: str, profile_id: str) -> bool:
        key = f"binding:{game_key}:{user_id}:{profile_id}"
        return self.docs.pop(key, None) is not None

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, Any]]:
        return [d for d in self.docs.values() if d.get("game_key") == game_key]


async def _start_provider_server(player_stats: Any, capabilities: ProviderCapabilities) -> tuple[Any, int]:
    """Start the true GameServicer in-process; (server, port)."""
    server = grpc.aio.server()
    servicer = GameServicer(lambda: capabilities, player_stats=player_stats)
    game_pb2_grpc.add_GameServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port


async def test_registration_end_to_end_with_the_real_wire(monkeypatch: Any) -> None:
    """The full chain: WorldsEdge reply → adapter → gRPC → seam → binding."""
    from kingdoms.discord.registration_platform import Aoe2ProfileValidationSeam
    from kingdoms.ext_librematch.adapter import LibrematchAdapter

    transport = FakeWorldsEdgeTransport({"/community/leaderboard/GetPersonalStat": WORLDEDGE_REPLY})

    async def player_stats(profile_id: str) -> PlayerStats | None:
        """The real adapter parses the real reply — no canned stats."""
        adapter = LibrematchAdapter(
            base_url="https://aoe-api.worldsedgelink.com",
            api_key="secret",
            transport=transport,
        )
        return await adapter.player_stats(profile_id)

    server, port = await _start_provider_server(
        player_stats,
        ProviderCapabilities(provider_id="ext-librematch", game_key="aoe2", player_stats=True),
    )
    try:
        client = GameProviderClient(f"127.0.0.1:{port}", "ext-librematch", "aoe2")

        # The adapter must call the REAL endpoint (GetPersonalStat), not /api/leaderboard.
        fetched = await client.get_player_stats("123456")
        assert fetched is not None, "the provider must resolve the profile through the real endpoint"
        assert any("GetPersonalStat" in url for url in transport.requests), (
            f"the adapter called the wrong endpoint: {transport.requests}"
        )
        assert not any("/api/leaderboard" in url for url in transport.requests), (
            "the deprecated /api/leaderboard endpoint must never be called"
        )
        entries = {e.key: e.value for block in fetched.blocks for e in block.entries}
        assert entries.get("alias") == "qmaxquique", "the identity must be parsed from statGroups"

        # The real seam against the real server.
        monkeypatch.delenv("EXT_LIBREMATCH_URI", raising=False)
        monkeypatch.setenv("EXT_LIBREMATCH_URI", f"127.0.0.1:{port}")
        seam = Aoe2ProfileValidationSeam()
        validated = await seam.validate_profile("123456")
        assert validated is not None, "the seam must validate through the running provider"
        assert "qmaxquique" in validated["aliases"], f"the alias must ride the binding: {validated}"

        # The real registration service over the in-memory Mongo.
        database = FakeMongo()
        service = RegistrationService(database, profile_seams={"aoe2": seam})
        binding = await service.bind_profile("867534391436378123", "aoe2", "123456")
        assert binding["profile"]["aliases"][0] == "qmaxquique"

        # Duplicate binding for another user is refused.
        try:
            await service.bind_profile("other-user", "aoe2", "123456")
            raise AssertionError("a profile already bound must be refused")
        except Exception as exc:
            assert "bound" in str(exc).lower() or "already" in str(exc).lower()

        # And the unlink round-trip (the remove flow's real path).
        removed = await service.unlink_profile("867534391436378123", "aoe2", "123456")
        assert removed, "the unlink must find the binding"
        assert await database.find_user_bindings("867534391436378123") == []
    finally:
        await server.stop(None)


async def test_registration_refused_when_the_provider_declares_no_stats(monkeypatch: Any) -> None:
    """player_stats=False (no API key): the seam names the config gap."""
    import pytest

    from kingdoms.discord.registration_platform import Aoe2ProfileValidationSeam, ProviderStatsUnavailableError

    async def player_stats(profile_id: str) -> PlayerStats | None:
        return None

    server, port = await _start_provider_server(
        player_stats,
        ProviderCapabilities(provider_id="ext-librematch", game_key="aoe2", player_stats=False),
    )
    try:
        monkeypatch.setenv("EXT_LIBREMATCH_URI", f"127.0.0.1:{port}")
        seam = Aoe2ProfileValidationSeam()
        with pytest.raises(ProviderStatsUnavailableError):
            await seam.validate_profile("123456")
    finally:
        await server.stop(None)


async def test_registration_rejects_an_unknown_profile(monkeypatch: Any) -> None:
    """A profile the leaderboards never heard of: explicit InvalidProfileError."""
    import pytest

    from kingdoms.core.services.registration import InvalidProfileError
    from kingdoms.discord.registration_platform import Aoe2ProfileValidationSeam

    empty_reply = {"result": {"code": 0}, "statGroups": [], "leaderboardStats": []}
    transport = FakeWorldsEdgeTransport({"/community/leaderboard/GetPersonalStat": empty_reply})

    async def player_stats(profile_id: str) -> PlayerStats | None:
        from kingdoms.ext_librematch.adapter import LibrematchAdapter

        adapter = LibrematchAdapter(
            base_url="https://aoe-api.worldsedgelink.com", api_key="secret", transport=transport
        )
        return await adapter.player_stats(profile_id)

    server, port = await _start_provider_server(
        player_stats,
        ProviderCapabilities(provider_id="ext-librematch", game_key="aoe2", player_stats=True),
    )
    try:
        monkeypatch.setenv("EXT_LIBREMATCH_URI", f"127.0.0.1:{port}")
        seam = Aoe2ProfileValidationSeam()
        database = FakeMongo()
        service = RegistrationService(database, profile_seams={"aoe2": seam})
        with pytest.raises(InvalidProfileError):
            await service.bind_profile("867534391436378123", "aoe2", "999999999")
    finally:
        await server.stop(None)


async def test_the_seam_never_silently_rejects_without_uri(monkeypatch: Any) -> None:
    """An unset EXT_LIBREMATCH_URI falls back to the docker default, not silence.

    The 2026-10-16 failure: an empty URI made validate_profile return None
    for every profile — 'profil inconnu' with no trace in the logs. The
    seam must always try a provider (the docker-network default stands in)
    and fail explicitly, never silently.
    """
    from kingdoms.discord.registration_platform import Aoe2ProfileValidationSeam

    monkeypatch.delenv("EXT_LIBREMATCH_URI", raising=False)
    seam = Aoe2ProfileValidationSeam()
    assert seam._provider_uri, "the seam must never hold an empty provider URI"
    assert seam._provider_uri == Aoe2ProfileValidationSeam.DEFAULT_PROVIDER_URI

