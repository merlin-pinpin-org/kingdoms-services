"""Unit tests for the ladder match-data service (kingdoms-services#205).

Enrichment of a completed match goes through the provider data cache:
one fetch ever per match (marker), 5-minute freshness on profiles,
daily sweep staleness. Degrades silently without providers.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.provider_cache import ProviderDataCache
from kingdoms.core.services.state import IStateStore, StateService
from kingdoms.mods.ladder.match_data import MatchDataService


class FakeStore(IStateStore):
    """In-memory state store honoring only_if_absent."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
        if only_if_absent and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None

    async def increment(self, key: str, window: int | None = None) -> int:
        return 1

    async def compare_delete(self, key: str, expected: str) -> bool:
        return False

    async def publish(self, channel: str, message: str) -> int:
        return 0

    async def subscribe(self, channel: str, callback: Any) -> None:
        return None

    async def unsubscribe(self, channel: str, callback: Any) -> None:
        return None


class FakeProvider:
    """Provider seam stub counting its calls."""

    provider_key = "librematch"

    def __init__(self, answer: dict[str, Any] | None) -> None:
        self.answer = answer
        self.calls = 0

    async def fetch_match(self, match_ref: str) -> dict[str, Any] | None:
        self.calls += 1
        return self.answer


DETAILS = {
    "match_ref": "508879537",
    "map_name": "Frigid Lake",
    "started_at": 1790262333,
    "ended_at": 1790263410,
    "match_kind": "lobby",
    "slots": [
        {"profile_id": "1143826", "faction_key": "incas", "team": 0, "filled": True},
        {"profile_id": "442163", "faction_key": "dravidians", "team": 1, "filled": True},
        {"profile_id": "", "faction_key": "", "team": 2, "filled": False},
    ],
    "options": (("victory_condition", "conquest"),),
}


def _service(answer: dict[str, Any] | None = DETAILS) -> tuple[MatchDataService, FakeProvider]:
    provider = FakeProvider(answer)
    cache = ProviderDataCache(StateService(store=FakeStore()))
    return MatchDataService(cache, live_provider=provider), provider


async def test_completed_match_enriched_once() -> None:
    service, provider = _service()
    extracted = await service.enrich_completed_match("508879537", {"_id": "m1"})
    assert extracted is not None
    assert extracted["map"] == "Frigid Lake"
    assert extracted["duration_s"] == 1077
    assert {c["faction_key"] for c in extracted["civs"]} == {"incas", "dravidians"}
    assert provider.calls == 1
    again = await service.enrich_completed_match("508879537", {"_id": "m1"})
    assert again is None
    assert provider.calls == 1


async def test_enrich_degrades_without_provider_answer() -> None:
    service, _ = _service(answer=None)
    extracted = await service.enrich_completed_match("unknown", {"_id": "m1"})
    assert extracted is None


async def test_extract_ignores_open_slots() -> None:
    service, _ = _service()
    extracted = service.extract(DETAILS)
    assert len(extracted["civs"]) == 2
    assert extracted["options"] == {"victory_condition": "conquest"}


async def test_profile_stats_five_minute_rule() -> None:
    service, _ = _service()
    calls = 0

    async def fetch_stats(profile_id: str) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"profile_id": profile_id, "rating": 1116}

    first = await service.player_stats("1143826", fetch_stats)
    assert first == {"profile_id": "1143826", "rating": 1116}
    cached = await service.player_stats("1143826", fetch_stats)
    assert cached == first
    assert calls == 1


async def test_daily_sweep_refetches_stale_only() -> None:
    service, _ = _service()
    fetched: list[str] = []

    async def fetch_stats(profile_id: str) -> dict[str, Any]:
        fetched.append(profile_id)
        return {"profile_id": profile_id}

    report = await service.sweep_registered_profiles(["a", "b"], fetch_stats)
    assert report.enriched == 2
    assert sorted(fetched) == ["a", "b"]
    fetched.clear()
    report = await service.sweep_registered_profiles(["a", "b"], fetch_stats)
    assert report.enriched == 0
    assert fetched == []
