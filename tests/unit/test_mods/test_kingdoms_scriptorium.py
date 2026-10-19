"""Kingdoms mod Scriptorium + restructuration cleanup — unit tests.

Patch restructuration-royaume: the pinned 📜 Scriptorium sommaire
carries the three rubric buttons (restart-proof), every rubric is an
ephemeral read-only view, and the legacy salons/categories retire at
provisioning time.
"""
from __future__ import annotations

import pathlib
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
        message = type(
            "Sent", (), {"content": content, "view": view, "embed": kwargs.get("embed")}
        )()
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


# ----------------------------------------------------------------------
# Rubric pagination (bug fix 17/10 — Cadastre/Diplomatie over 2000 chars)
# ----------------------------------------------------------------------


def test_paginate_keeps_every_page_under_discord_limit() -> None:
    """Long rubrics split into pages under Discord's 2000-char limit."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _paginate

    lines = [f"- **Map {index}** — ⛰️ 🌞 · Offre un contre-espionnage/semaine" for index in range(200)]
    pages = _paginate(["# 🗺️ Cadastre", "", *lines])
    assert len(pages) > 1
    assert all(len(page) <= 1800 for page in pages)
    rejoined = "\n".join(pages)
    for index in (0, 100, 199):
        assert f"**Map {index}**" in rejoined


def test_paginate_returns_one_page_for_short_content() -> None:
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _paginate

    assert _paginate(["# titre", "", "une ligne"]) == ["# titre\n\nune ligne"]
    assert _paginate([]) == [""]


async def test_rubric_pages_fit_the_real_catalog() -> None:
    """The real 96-map catalog produces pages Discord accepts."""
    from kingdoms.mods.kingdoms.config import default_season_config
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _rubric_pages, _strings

    config = default_season_config()
    assert len(config.maps) == 96  # the live catalog Drasah reported

    service = _SimpleNS(config=config)

    pages = await _rubric_pages("cadastre", _strings("fr"), service)
    assert len(pages) > 1
    assert all(len(page) <= 2000 for page in pages)
    joined = "\n".join(pages)
    assert any("Acropolis" in page for page in pages)
    assert "Bois" in joined


async def test_rubric_pager_turns_pages_bounded() -> None:
    """The ⬅️➡️ pager edits the ephemeral in place and clamps the bounds."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _paginate, _RubricPager

    pages = _paginate(["# titre", *[f"ligne {i}" for i in range(300)]])
    view = _RubricPager(pages, 0)
    assert len(pages) > 1
    children = list(view.children)
    assert len(children) == 3  # ◀️ · 1/N · ▶️
    first, _counter, last = children
    assert first.disabled is True  # page 1: no going back
    assert last.disabled is False

    turned = view._go(1)
    interaction = MockInteraction(user=MockUser(id="u"))
    await turned(interaction)
    # MockInteraction stores the edited message: page 2 content and fresh pager view
    edited = interaction.response.message
    assert edited is not None
    assert edited.content == pages[1]
    new_view = edited.view
    assert new_view is not None and new_view is not view
    assert any(child.label == f"2/{len(pages)}" for child in new_view.children)
    # turning past the last page clamps
    clamped = new_view._go(len(pages))
    await clamped(interaction)
    assert interaction.response.message.content == pages[-1]


@pytest.mark.asyncio
async def test_rubric_pages_lead_with_the_practical_intro() -> None:
    """scriptorium-v2: each rubric opens with its « à quoi ça sert » chapeau."""
    from kingdoms.mods.kingdoms.config import default_season_config
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _rubric_pages, _strings

    config = default_season_config()
    service = _SimpleNS(config=config)
    pages = await _rubric_pages("cadastre", _strings("fr"), service)
    assert pages[0].startswith("🗺️ **À quoi ça sert**")
    pages_en = await _rubric_pages("cadastre", _strings("en"), service)
    assert pages_en[0].startswith("🗺️ **What it's for**")


async def test_scriptorium_artwork_falls_back_to_the_bundled_b64() -> None:
    """Without assets/scriptorium.png the bundled base64 artwork decodes."""
    import base64

    from kingdoms.mods.kingdoms.kingdom_scriptorium import _scriptorium_artwork

    artwork = _scriptorium_artwork()
    assert artwork is not None
    assert artwork.read_bytes()[:8] == base64.b64decode("iVBORw0KGgo=")  # PNG magic


async def test_deploy_scriptorium_panel_illustrates_the_sommaire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """With the artwork shipped the sommaire embeds the illustration."""
    import kingdoms.mods.kingdoms.kingdom_scriptorium as scriptorium

    artwork = tmp_path / "scriptorium.png"
    artwork.write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(scriptorium, "_scriptorium_artwork", lambda: artwork)

    channel = _FakeChannel()
    ok = await scriptorium.deploy_scriptorium_panel(channel, "fr")  # type: ignore[arg-type]
    assert ok is True
    assert len(channel.sent) == 1
    embed = channel.sent[0].embed
    assert embed is not None
    assert embed.image is not None
    assert "attachment://scriptorium.png" in str(embed.image.url)


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


# ----------------------------------------------------------------------
# restructuration-serveur-v1 d (Drasah 19/10): the 📖 Règles and
# 🎤 Présentation buttons replace the retired forum/salon.
# ----------------------------------------------------------------------

_SELECT_TYPE = __import__("discord").ui.Select  # the rubric select of the bubble


def test_sommaire_carries_the_rules_and_presentation_buttons() -> None:
    """The pinned sommaire now carries the 5 buttons (3 rubrics + 2 d)."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import (
        KingdomPresentationButton,
        KingdomRulesButton,
        _scriptorium_view,
        _strings,
    )

    view = _scriptorium_view(_strings("fr"))
    ids = [item.custom_id for item in view.children]
    assert "kingdoms:rules:open" in ids
    assert "kingdoms:presentation:open" in ids
    labels = [
        getattr(getattr(item, "item", item), "label", None) for item in view.children
    ]
    assert any(label and "Règles" in label for label in labels)
    assert any(label and "Présentation" in label for label in labels)
    # The buttons are restart-proof DynamicItems with stable custom ids.
    rules = next(
        item for item in view.children if item.custom_id == "kingdoms:rules:open"
    )
    assert isinstance(rules, KingdomRulesButton)
    presentation = next(
        item for item in view.children
        if item.custom_id == "kingdoms:presentation:open"
    )
    assert isinstance(presentation, KingdomPresentationButton)


def test_rules_pages_serve_the_live_guide_without_forum_markers() -> None:
    """_rules_pages keeps the 10 rubrics, strips the -# markers, paginates."""
    from kingdoms.mods.kingdoms.config import default_season_config
    from kingdoms.mods.kingdoms.kingdom_guide import GUIDE_MARKER_PREFIX
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _rules_pages

    options, pages = _rules_pages(default_season_config())
    assert len(options) == 10
    assert len(options) <= 25  # Discord's select ceiling
    keys = {key for key, _title in options}
    assert set(pages) == keys
    for _key, title in options:
        assert title and len(title) <= 100
    for _key, rubric_pages in pages.items():
        assert rubric_pages
        for page in rubric_pages:
            assert len(page) <= 1800
            for line in page.split("\n"):
                assert not line.lstrip().startswith("-#")
                assert GUIDE_MARKER_PREFIX not in line


async def test_rules_button_opens_the_ephemeral_guide_bubble() -> None:
    """The 📖 Règles button serves the live guide without any wiring."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import KingdomRulesButton

    button = KingdomRulesButton("📖 Règles")
    interaction = MockInteraction(
        user=MockUser(id="u"), locale="fr", custom_id="kingdoms:rules:open"
    )
    await button.callback(interaction)  # type: ignore[arg-type]
    assert interaction.response.deferred is True
    assert len(interaction.followup.messages) == 1
    message = interaction.followup.messages[0]
    assert message.view is not None
    # The bubble leads with the first rubric's first page (Bienvenue).
    assert message.content
    children = list(message.view.children)
    assert children and isinstance(children[0], _SELECT_TYPE)

async def test_rules_button_rebuilds_in_both_locales() -> None:
    """The rules button rebuilds from its custom id in fr and en."""
    import re as _re

    from kingdoms.mods.kingdoms.kingdom_scriptorium import KingdomRulesButton

    match = _re.search(r"kingdoms:rules:open", "kingdoms:rules:open")
    assert match is not None
    for locale in ("fr", "en-US"):
        interaction = MockInteraction(user=MockUser(id="u"), locale=locale)  # type: ignore[arg-type]
        button = await KingdomRulesButton.from_custom_id(
            interaction, type("Item", (), {})(), match  # type: ignore[arg-type]
        )
        assert button.item.custom_id == "kingdoms:rules:open"
        assert button.item.label


async def test_presentation_button_opens_the_pitch() -> None:
    """The 🎤 Présentation button serves the designer's pitch ephemerally."""
    from kingdoms.mods.kingdoms.kingdom_content import PRESENTATION_MESSAGE
    from kingdoms.mods.kingdoms.kingdom_scriptorium import KingdomPresentationButton

    button = KingdomPresentationButton("🎤 Présentation")
    interaction = MockInteraction(
        user=MockUser(id="u"), locale="fr", custom_id="kingdoms:presentation:open"
    )
    await button.callback(interaction)  # type: ignore[arg-type]
    assert interaction.response.deferred is True
    assert len(interaction.followup.messages) == 1
    message = interaction.followup.messages[0]
    assert message.content
    assert message.content in PRESENTATION_MESSAGE or message.content.startswith(
        PRESENTATION_MESSAGE[:40]
    )


async def test_rules_guide_view_pager_turns_pages_bounded() -> None:
    """The guide view pages a long rubric with ⬅️/counter/➡️ re-renders."""
    from kingdoms.mods.kingdoms.kingdom_scriptorium import _RulesGuideView, _strings

    options = [("a", "Rubrique A"), ("b", "Rubrique B")]
    pages = {
        "a": ["page 1", "page 2", "page 3"],
        "b": ["seule"],
    }
    view = _RulesGuideView(options, pages, _strings("fr"))
    assert view.content() == "page 1"
    buttons = [item for item in view.children if hasattr(item, "label")]
    labels = [item.label for item in buttons]
    assert "◀️" in labels and "▶️" in labels and "1/3" in labels
    next_button = next(item for item in buttons if item.label == "▶️")
    assert next_button.disabled is False
    prev_button = next(item for item in buttons if item.label == "◀️")
    assert prev_button.disabled is True
    # Turning to page 2 re-renders via edit_message.
    turn = MockInteraction(user=MockUser(id="u"), locale="fr")
    await next_button.callback(turn)  # type: ignore[arg-type]
    assert turn.response.message is not None
    assert turn.response.message.content == "page 2"
