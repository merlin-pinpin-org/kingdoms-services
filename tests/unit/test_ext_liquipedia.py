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
        self.batch_calls: list[list[str]] = []

    def map_data(self, page: str) -> Any:
        self.calls.append(page)
        if "Team Islands" in page:
            return _FakeData()
        raise RuntimeError("no such page")

    def map_data_batch(self, pages: list[str]) -> dict[str, Any]:
        """The batched contract the seed worker rides (<=50 per call)."""
        self.batch_calls.append(list(pages))
        result: dict[str, Any] = {}
        for page in pages:
            if "Team Islands" in page:
                key = page.strip().replace(" ", "_")
                result[key] = _FakeData()
        return result


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
    source = LiquipediaSource({"aoe2": upstream}, db)  # type: ignore[arg-type]
    seed = source.map_seed("aoe2", "Team Islands")
    assert seed is not None
    assert seed["name"] == "Team Islands"
    assert seed["image_url"].endswith(".png")
    assert "aoe2:Team Islands" in db.maps.docs
    assert db.maps.docs["aoe2:Team Islands"]["descriptor"]["name"] == "Team Islands"


async def test_map_seed_unknown_page_is_none() -> None:
    source = LiquipediaSource({"aoe2": _FakeUpstream()}, _FakeDB())  # type: ignore[arg-type]
    assert source.map_seed("aoe2", "Nonexistent Map") is None


def test_faction_surface_is_empty() -> None:
    source = LiquipediaSource({"aoe2": _FakeUpstream()}, _FakeDB())  # type: ignore[arg-type]
    assert source.faction_keys() == []
    assert source.faction_content("Franks", "en") is None


def test_gallery_icon_accepts_the_file_prefix_form() -> None:
    """A gallery entry with the File: prefix parses (the historical form)."""
    from kingdoms.ext_liquipedia.liquipedia import LiquipediaMapsProvider

    wikitext = "== Gallery ==\n<gallery>\nFile:Arabiade icon.png|In-game icon\nFile:Arabia map.png|Overview\n</gallery>"
    assert LiquipediaMapsProvider._parse_gallery_icon(wikitext) == "Arabiade icon.png"


def test_gallery_icon_accepts_the_bare_mediawiki_form() -> None:
    """A gallery entry WITHOUT the File: prefix parses (Arena's form)."""
    from kingdoms.ext_liquipedia.liquipedia import LiquipediaMapsProvider

    wikitext = "== Gallery ==\n<gallery>\nArenaicon.png|In-game icon\nArena aoe2de map.png|AoE2:DE map\n</gallery>"
    assert LiquipediaMapsProvider._parse_gallery_icon(wikitext) == "Arenaicon.png"


def test_gallery_icon_prefers_the_captioned_icon_entry() -> None:
    """The entry captioned 'icon' wins over the first entry."""
    from kingdoms.ext_liquipedia.liquipedia import LiquipediaMapsProvider

    wikitext = "<gallery>\nArena minimap.png|Minimap\nArenaicon.png|In-game icon\n</gallery>"
    assert LiquipediaMapsProvider._parse_gallery_icon(wikitext) == "Arenaicon.png"


def test_gallery_icon_is_empty_without_a_gallery() -> None:
    """A page without a Gallery yields no icon (the main image takes over)."""
    from kingdoms.ext_liquipedia.liquipedia import LiquipediaMapsProvider

    assert LiquipediaMapsProvider._parse_gallery_icon("No gallery here.") == ""
