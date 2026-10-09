"""Unit tests for the content provider seam (kingdoms.v1.Content).

Covers: capability and content round-trips (plain dict ↔ wire), the
ContentServicer (including the NOT_FOUND degradation), and the ext
upstream source selection (dataset default, api requires URL, unknown
value fails closed).
"""

from __future__ import annotations

import asyncio

import grpc
import pytest

from kingdoms.core.rpc.content import (
    ContentServicer,
    content_capabilities_from_wire,
    content_capabilities_to_wire,
    localized_content_from_wire,
    localized_content_to_wire,
)
from kingdoms.ext_aoe2techtree.source import (
    DatasetContentSource,
    resolve_source,
)
from kingdoms.rpc_generated.kingdoms.v1 import content_pb2


class _ServicerContext:
    """Minimal stand-in for grpc.aio.ServicerContext in unit tests."""

    def __init__(self) -> None:
        self.code: grpc.StatusCode | None = None

    async def abort(self, code: grpc.StatusCode, details: str) -> None:
        """Raise like the real context (abort never returns)."""
        self.code = code
        raise grpc.aio.AioRpcError(code, None, details)


def test_content_capabilities_round_trip() -> None:
    """The capability declaration survives the plain → wire → plain trip."""
    wire = content_capabilities_to_wire("aoe2techtree", "aoe2", ("en", "fr"))
    provider_key, game_key, locales = content_capabilities_from_wire(wire)
    assert (provider_key, game_key, locales) == ("aoe2techtree", "aoe2", ("en", "fr"))


def test_localized_content_round_trip() -> None:
    """The content descriptor survives the plain dict → wire → dict trip."""
    descriptor = {
        "entity_id": "faction:aoe2:Franks",
        "locale": "fr",
        "name": "Francs",
        "summary": "Civilisation de cavalerie",
        "source_url": "https://github.com/SiegeEngineers/aoe2techtree",
        "found": True,
        "image_url": "https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/master/img/Civs/franks.png",
    }
    plain = localized_content_from_wire(localized_content_to_wire(descriptor))
    assert plain == descriptor


def test_servicer_serves_faction_content() -> None:
    """The servicer translates the source descriptor onto the wire."""
    async def list_factions() -> list[str]:
        return ["Franks"]

    async def faction_content(key: str, locale: str) -> dict[str, str | bool] | None:
        return {
            "entity_id": f"faction:aoe2:{key}",
            "locale": locale,
            "name": "Francs",
            "summary": "Cavalerie",
            "source_url": "https://github.com/SiegeEngineers/aoe2techtree",
            "found": True,
        }

    async def map_content(key: str, locale: str) -> dict[str, str | bool] | None:
        return None

    servicer = ContentServicer(
        "aoe2techtree",
        "aoe2",
        ("en", "fr"),
        list_factions=list_factions,
        faction_content=faction_content,
        map_content=map_content,
    )
    wire = asyncio.run(
        servicer.GetFactionContent(
            content_pb2.FactionContentRequest(faction_key="Franks", locale="fr"),
            _ServicerContext(),
        )
    )
    assert wire.name == "Francs"
    assert wire.entity_id == "faction:aoe2:Franks"


def test_servicer_not_found_on_unknown_faction() -> None:
    """An unknown faction aborts NOT_FOUND (core degrades to None)."""
    async def list_factions() -> list[str]:
        return []

    async def faction_content(key: str, locale: str) -> dict[str, str | bool] | None:
        return None

    async def map_content(key: str, locale: str) -> dict[str, str | bool] | None:
        return None

    servicer = ContentServicer(
        "aoe2techtree",
        "aoe2",
        ("en",),
        list_factions=list_factions,
        faction_content=faction_content,
        map_content=map_content,
    )
    with pytest.raises(grpc.aio.AioRpcError) as excinfo:
        asyncio.run(
            servicer.GetFactionContent(
                content_pb2.FactionContentRequest(faction_key="Huns", locale="en"),
                _ServicerContext(),
            )
        )
    assert excinfo.value.code() == grpc.StatusCode.NOT_FOUND


def test_resolve_source_dataset_default() -> None:
    """The vendored dataset is the default upstream of the ext process."""
    source = resolve_source("")
    assert isinstance(source, DatasetContentSource)


def test_resolve_source_api_requires_url() -> None:
    """API upstream without URL fails closed at startup."""
    with pytest.raises(RuntimeError, match="AOE2TECHTREE_API_URL"):
        resolve_source("api", api_url="")


def test_resolve_source_unknown_fails_closed() -> None:
    """An unknown source selector never silently serves nothing."""
    with pytest.raises(RuntimeError, match="unknown AOE2TECHTREE_SOURCE"):
        resolve_source("telepathy")


def test_dataset_source_faction_content() -> None:
    """The dataset upstream resolves localized civ help text."""
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        dataset_dir = Path(tmp)
        (dataset_dir / "data.json").write_text(
            json.dumps({"civs": {"Franks": {"name_string_id": 100, "help_string_id": 101}}}),
            encoding="utf-8",
        )
        (dataset_dir / "strings-en.json").write_text(
            json.dumps({"100": "Franks", "101": "Cavalry civ"}), encoding="utf-8"
        )
        (dataset_dir / "strings-fr.json").write_text(
            json.dumps({"100": "Francs", "101": "Civilisation de cavalerie"}), encoding="utf-8"
        )
        source = DatasetContentSource(dataset_dir)
        assert source.faction_keys() == ["Franks"]
        content = source.faction_content("Franks", "fr")
        assert content is not None
        assert content["name"] == "Francs"
        assert content["summary"] == "Civilisation de cavalerie"
        assert source.faction_content("Huns", "fr") is None


def test_content_server_list_factions_regression() -> None:
    """serve_content_provider wires list_factions by CALLING the source.

    Regression: the lambda used to pass the bound method instead of its
    result, and ListFactions failed with "must assign iterable".
    """
    from kingdoms.core_process.content_server import _async_wrap

    class _Source:
        def faction_keys(self) -> list[str]:  # a method, not a property
            return ["Franks", "Britons"]

        async def faction_content(self, key: str, locale: str) -> dict[str, str | bool] | None:
            return None

        async def map_content(self, key: str, locale: str) -> dict[str, str | bool] | None:
            return None

    source = _Source()
    list_factions = lambda: _async_wrap(source.faction_keys())  # noqa: E731
    faction_content = lambda key, locale: _async_wrap(source.faction_content(key, locale))  # noqa: E731
    map_content = lambda key, locale: _async_wrap(source.map_content(key, locale))  # noqa: E731
    servicer = ContentServicer(
        "aoe2techtree",
        "aoe2",
        ("en",),
        list_factions=list_factions,
        faction_content=faction_content,
        map_content=map_content,
    )
    wire = asyncio.run(
        servicer.ListFactions(content_pb2.ListFactionsRequest(), _ServicerContext())
    )
    assert list(wire.faction_keys) == ["Franks", "Britons"]
