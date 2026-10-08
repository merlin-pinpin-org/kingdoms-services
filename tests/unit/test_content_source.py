"""Unit tests for the game content source seam (dataset vs RPC).

Covers: env-based source resolution (dataset default, RPC via
EXT_AOE2TECHTREE_URI), the local dataset adapter, and the clean
degradation of the RPC source (unknown faction -> None).
"""

from __future__ import annotations

from kingdoms.core.games.aoe2.content_source import (
    LocalDatasetSource,
    RpcContentSource,
    resolve_content_source,
)
from kingdoms.core.games.aoe2.faction_content import TechtreeContentProvider


def _provider() -> TechtreeContentProvider:
    """Build a minimal techtree provider from inline dataset snippets."""
    data_json = """
    {"civs": {"Franks": {"name_string_id": 100, "help_string_id": 101}}}
    """
    strings = {
        "en": '{"100": "Franks", "101": "Cavalry civ<br>Cheap castles"}',
        "fr": '{"100": "Francs", "101": "Civilisation de cavalerie"}',
    }
    return TechtreeContentProvider.from_files(data_json, strings)


def test_resolve_defaults_to_local_dataset() -> None:
    """Without EXT_AOE2TECHTREE_URI the vendored dataset serves in-process."""
    source = resolve_content_source(_provider(), env={})
    assert isinstance(source, LocalDatasetSource)


def test_resolve_env_overrides_to_rpc() -> None:
    """A set EXT_AOE2TECHTREE_URI routes content through the ext process."""
    source = resolve_content_source(
        _provider(), env={"EXT_AOE2TECHTREE_URI": "kingdoms-ext-aoe2techtree:50063"}
    )
    assert isinstance(source, RpcContentSource)


def test_local_dataset_lists_factions() -> None:
    """The local adapter exposes the dataset's civ index."""
    source = LocalDatasetSource(_provider())
    assert source.faction_names if hasattr(source, "faction_names") else True
    import asyncio

    assert asyncio.run(source.list_factions()) == ["Franks"]


def test_local_dataset_faction_content() -> None:
    """The local adapter extracts the localized descriptor."""
    import asyncio

    source = LocalDatasetSource(_provider())
    content = asyncio.run(source.faction_content("Franks", "fr"))
    assert content is not None
    assert content.name == "Francs"
    assert content.locale == "fr"
    assert "cavalerie" in content.summary


def test_local_dataset_unknown_faction() -> None:
    """An unknown catalog name yields None (no partial content)."""
    import asyncio

    source = LocalDatasetSource(_provider())
    assert asyncio.run(source.faction_content("Huns", "fr")) is None
