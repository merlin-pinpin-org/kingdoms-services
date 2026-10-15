"""Kingdoms mod Scriptorium + restructuration cleanup — unit tests.

Patch restructuration-royaume: the pinned 📜 Scriptorium sommaire
carries the three rubric buttons (restart-proof), every rubric is an
ephemeral read-only view, and the legacy salons/categories retire at
provisioning time.
"""
from __future__ import annotations

import re
from types import SimpleNamespace as _SimpleNS
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import MockInteraction, MockUser

from .test_kingdoms_attack_phases import PhasesBundle


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edited: dict[str, Any] | None = None

    async def edit(self, **kwargs: Any) -> None:
        self.edited = kwargs


class _History:
    def __init__(self, items: list[_FakeMessage]) -> None:
        self.items = items

    def __aiter__(self) -> _History:
        return self

    async def __anext__(self) -> _FakeMessage:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class _FakeChannel:
    def __init__(self, name: str = "📜 Scriptorium") -> None:
        self.name = name
        self.history_list: list[_FakeMessage] = []
        self.sent: list[Any] = []

    def history(self, limit: int = 50) -> _History:
        return _History(list(self.history_list))

    async def send(
        self, content: str | None = None, *, view: Any = None, **kwargs: Any
    ) -> Any:
        message = type("Sent", (), {"content": content, "view": view})()
        self.sent.append(message)
        return message


def _wiring(bundle: PhasesBundle) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=bundle.kingdoms,
            attacks_service=bundle.attacks,
            territories_service=bundle.territories,
        )
    )


async def test_deploy_scriptorium_panel_pins_the_sommaire() -> None:
    """The deployment pins the marker with the three rubric buttons."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import (
        SCRIPTORIUM_MARKER,
        deploy_scriptorium_panel,
    )

    channel = _FakeChannel()
    marked = _FakeMessage(f"old <!-- {SCRIPTORIUM_MARKER} -->")
    channel.history_list = [marked]
    ok = await deploy_scriptorium_panel(channel, "fr")  # type: ignore[arg-type]
    assert ok is True
    assert channel.sent == []  # the marked sommaire is edited, never duplicated
    assert marked.edited is not None
    content = str(marked.edited.get("content") or "")
    assert SCRIPTORIUM_MARKER in content
    view = marked.edited.get("view")
    ids = [item.custom_id for item in view.children] if view is not None else []
    assert "kingdoms:scriptorium:cadastre" in ids
    assert "kingdoms:scriptorium:diplomatie" in ids
    assert "kingdoms:scriptorium:seigneurie" in ids


async def test_scriptorium_button_rebuilds_in_both_locales() -> None:
    """The persistent button rebuilds from its custom id in fr and en."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import KingdomScriptoriumButton

    match = re.search(
        r"kingdoms:scriptorium:(?P<topic>cadastre|diplomatie|seigneurie)",
        "kingdoms:scriptorium:cadastre",
    )
    assert match is not None
    for locale in ("fr", "en"):
        interaction = MockInteraction(user=MockUser(id="u"), locale=locale)  # type: ignore[arg-type]
        button = await KingdomScriptoriumButton.from_custom_id(
            interaction, type("Item", (), {})(), match  # type: ignore[arg-type]
        )
        assert button.topic == "cadastre"
        assert button.item.label == "🗺️ Cadastre"


async def test_seigneurie_rubric_lists_budgets() -> None:
    """The Seigneurie rubric lists every lord with the remaining budgets."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _seigneurie_content, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    kingdoms = await bundle.kingdoms.kingdoms()
    lords = await bundle.kingdoms.lords()
    content = _seigneurie_content(_strings("fr"), kingdoms, lords, 1, 1)
    assert "Seigneurie" in content
    assert "⚔️ 1/1 · 🛡️ 1/1" in content


async def test_cadastre_rubric_shows_bonus() -> None:
    """The Cadastre rubric lists the catalog maps with their bonus."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _cadastre_content, _strings

    entry = type("Entry", (), {})()
    entry.display_name = "Acropolis"
    entry.key = "acropolis"
    entry.cadastre = {"bonus": "Offre un contre-espionnage/semaine"}
    entry.types = _SimpleNS(montagne=True)

    content = _cadastre_content(_strings("fr"), [entry])
    assert "Acropolis" in content
    assert "contre-espionnage" in content
    assert "⛰️" in content


@pytest.mark.asyncio
async def test_cleanup_legacy_structure_retires_salons_and_categories() -> None:
    """The cleanup removes the legacy salons and the retired categories."""
    from kingdoms.mods.kingdoms.kingdom_setup import cleanup_legacy_structure
    from tests.mocks.discord_mock import MockGuild

    guild = MockGuild()
    epoch = await guild.create_text_channel("🏺 Âge")
    await guild.create_text_channel("Seigneurs")
    await guild.create_text_channel("Diplomatie")
    await guild.create_text_channel("Cadastre")
    removed = await cleanup_legacy_structure(guild, str(epoch.id))
    assert "Seigneurs" in removed
    assert "Diplomatie" in removed
    assert "Cadastre" in removed
    remaining = [c.name for c in guild.text_channels]
    assert "Seigneurs" not in remaining
    assert "Diplomatie" not in remaining
    assert "Cadastre" not in remaining
    assert "🏺 Âge" in remaining
