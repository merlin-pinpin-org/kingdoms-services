"""Unit tests for the ext-librematch live adapter.

The adapter maps raw Worlds Edge Link lobby payloads to MatchDetails,
decoding the base64+zlib slotinfo/options blobs; decode failures degrade
to serving the rest of the match data.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any

import httpx
import pytest

from kingdoms.core.games.aoe2.blobs import decode_blob  # noqa: F401 - cross-ref
from kingdoms.core.models.game import MatchDetails, Slot
from kingdoms.ext_librematch.adapter import LibrematchAdapter


def _blob(payload: dict[str, Any]) -> str:
    """Encode a payload as the APIs do: base64(zlib(json))."""
    return base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()


def _lobby(match_id: str = "777") -> dict[str, Any]:
    """Build a raw lobby payload with blobs, as served by the API."""
    return {
        "advertiserId": match_id,
        "mapname": "Arabia",
        "slotinfo": _blob(
            {
                "slots": [
                    {"slot_index": 0, "profile_id": "p1", "civ": 1, "team": 1, "filled": True},
                    {"slot_index": 1, "profile_id": "p2", "civ": 7, "team": 2, "filled": True},
                ]
            }
        ),
        "options": _blob({"map_size": "huge", "speed": "standard"}),
    }


class _FakeTransport(httpx.AsyncBaseTransport):
    """Serve canned JSON responses without any network."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Return the canned payload for every request."""
        return httpx.Response(200, json=self._payload)


def _adapter(payload: object) -> LibrematchAdapter:
    return LibrematchAdapter(base_url="https://test", transport=_FakeTransport(payload))


@pytest.mark.asyncio
async def test_match_details_decodes_blobs() -> None:
    """Slotinfo and options blobs are decoded into the MatchDetails."""
    adapter = _adapter([_lobby("777")])
    details = await adapter.match_details("777")
    assert details == MatchDetails(
        match_ref="777",
        map_name="Arabia",
        slots=(
            Slot(slot_index=0, profile_id="p1", faction_key="1", team=1, filled=True, slot_kind="human"),
            Slot(slot_index=1, profile_id="p2", faction_key="7", team=2, filled=True, slot_kind="human"),
        ),
        options=(("map_size", "huge"), ("speed", "standard")),
        started_at=0,
        match_kind="lobby",
    )


@pytest.mark.asyncio
async def test_match_details_unknown_match_returns_none() -> None:
    """An unknown match_ref degrades to None (not an error)."""
    adapter = _adapter([_lobby("777")])
    assert await adapter.match_details("unknown") is None


@pytest.mark.asyncio
async def test_undecodable_blob_degrades_to_empty() -> None:
    """An undecodable slotinfo blob serves the rest of the match."""
    lobby = _lobby("777")
    lobby["slotinfo"] = "bm90LXpsaWI="
    adapter = _adapter([lobby])
    details = await adapter.match_details("777")
    assert details is not None
    assert details.map_name == "Arabia"
    assert details.slots == ()
    assert details.options == (("map_size", "huge"), ("speed", "standard"))


@pytest.mark.asyncio
async def test_list_maps_serves_distinct_lobby_maps() -> None:
    """The map catalog is the distinct maps across the current lobbies."""
    other = _lobby("778")
    other["mapname"] = "Arena"
    adapter = _adapter([_lobby("777"), other])
    maps = await adapter.list_maps()
    assert {m.map_key for m in maps} == {"Arabia", "Arena"}


@pytest.mark.asyncio
async def test_player_stats_without_api_key_degrades_to_none() -> None:
    """No configured API key: stats degrade to None (manual path)."""
    adapter = LibrematchAdapter(base_url="https://test", transport=_FakeTransport({}))
    assert await adapter.player_stats("12345") is None


@pytest.mark.asyncio
async def test_player_stats_serves_leaderboard_blocks() -> None:
    """With a key and a leaderboard reply, stats come back as blocks."""
    payload = {
        "result": [
            {
                "profileId": "12345",
                "rank": 42,
                "rating": 1600,
                "wins": 30,
                "losses": 25,
                "streak": 3,
                "games": 55,
            }
        ]
    }
    adapter = LibrematchAdapter(
        base_url="https://test", transport=_FakeTransport(payload), api_key="secret"
    )
    stats = await adapter.player_stats("12345")
    assert stats is not None
    assert stats.profile_id == "12345"
    assert stats.blocks, "at least one leaderboard block"
    block = stats.blocks[0]
    entries = {e.key: e.value for e in block.entries}
    assert entries["rank"] == "42"
    assert entries["rating"] == "1600"
    assert entries["wins"] == "30"


@pytest.mark.asyncio
async def test_player_stats_unknown_profile_returns_none() -> None:
    """A leaderboard reply without the profile degrades to None."""
    payload = {"result": [{"profileId": "other", "rank": 1}]}
    adapter = LibrematchAdapter(
        base_url="https://test", transport=_FakeTransport(payload), api_key="secret"
    )
    assert await adapter.player_stats("12345") is None
