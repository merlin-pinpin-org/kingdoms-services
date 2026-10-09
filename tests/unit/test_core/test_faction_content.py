"""TechtreeContentProvider tests: FR/EN extraction from the vendored dataset."""

from __future__ import annotations

from pathlib import Path

from kingdoms.core.games.aoe2.faction_content import (
    PROVIDER_KEY,
    SUPPORTED_LOCALES,
    TechtreeContentProvider,
)

DATASET_DIR = Path(__file__).resolve().parents[3] / "data" / "core" / "aoe2techtree"


def _provider() -> TechtreeContentProvider:
    data_json = (DATASET_DIR / "data.json").read_text(encoding="utf-8")
    locale_files = {lng: (DATASET_DIR / f"strings-{lng}.json").read_text(encoding="utf-8") for lng in SUPPORTED_LOCALES}
    return TechtreeContentProvider.from_files(data_json, locale_files)


def test_provider_extracts_french_faction_content() -> None:
    content = _provider().faction_content("Franks", "fr")
    assert content is not None
    assert content.entity_id == "faction:aoe2:Franks"
    assert content.locale == "fr"
    assert content.name == "Francs"
    assert "civilisation" in content.summary.lower()
    assert content.source_url == "https://aoe2techtree.net/#Franks"


def test_provider_extracts_english_faction_content() -> None:
    content = _provider().faction_content("Franks", "en")
    assert content is not None
    assert content.name == "Franks"
    assert "civilization" in content.summary.lower()


def test_provider_returns_none_for_unknown_faction() -> None:
    assert _provider().faction_content("NoSuchFaction", "fr") is None


def test_provider_lists_dataset_factions() -> None:
    names = _provider().faction_names()
    assert "Franks" in names
    assert len(names) >= 40


def test_provider_key_is_stable() -> None:
    assert PROVIDER_KEY == "aoe2techtree"
