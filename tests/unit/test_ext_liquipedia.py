"""Unit tests for the ext-liquipedia source (Content contract seam)."""

from __future__ import annotations

from typing import Any

from kingdoms.ext_liquipedia.source import LiquipediaSource


class _FakeInfo:
    name = "Team Islands"
    description = "A classic water map."
    terrain = "Water"
    size = "2-8"
    map_type = "Water"
    source_url = "https://liquipedia.net/ageofempires/Team_Islands"


class _FakeData:
    info = _FakeInfo()
    image_url = "https://liquipedia.net/x.png"
    icon_url = ""


class _FakeUpstream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def map_data(self, page: str) -> Any:
        self.calls.append(page)
        if "Team Islands" in page:
            return _FakeData()
        raise RuntimeError("no such page")


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return None

    def update_one(self, doc_id: dict[str, Any], update: dict[str, Any], upsert: bool) -> None:
        self.docs[doc_id["_id"]] = dict(update.get("$set", {}))


class _FakeDB:
    def __init__(self) -> None:
        self.maps = _FakeCollection()
        self.pages = _FakeCollection()

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.maps if name.endswith("_maps") else self.pages


async def test_map_seed_fetches_and_persists() -> None:
    upstream = _FakeUpstream()
    db = _FakeDB()
    source = LiquipediaSource(upstream, db)  # type: ignore[arg-type]
    seed = source.map_seed("aoe2", "Team Islands")
    assert seed is not None
    assert seed["name"] == "Team Islands"
    assert seed["image_url"].endswith(".png")
    assert "aoe2:Team Islands" in db.maps.docs
    assert db.maps.docs["aoe2:Team Islands"]["descriptor"]["name"] == "Team Islands"


async def test_map_seed_unknown_page_is_none() -> None:
    source = LiquipediaSource(_FakeUpstream(), _FakeDB())  # type: ignore[arg-type]
    assert source.map_seed("aoe2", "Nonexistent Map") is None


def test_faction_surface_is_empty() -> None:
    source = LiquipediaSource(_FakeUpstream(), _FakeDB())  # type: ignore[arg-type]
    assert source.faction_keys() == []
    assert source.faction_content("Franks", "en") is None
