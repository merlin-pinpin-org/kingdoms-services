"""Unit tests for the Royaume admin panel section (kingdoms#214 tranche ②-a).

The section registers into the pinned panel's mod seam, builds its
Components V2 view from the live season, and routes every action
through the D75 reason modal — the socle re-checks the reason, so
these tests prove the wiring: the namespace ids, the unwired
degradation, the view composition and one full run of each season
operation (recruitment, applications, quotas, foundation) through the
real KingdomAdminService over the in-memory store.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
import pytest

import kingdoms.discord.kingdoms_admin_panel as panel
from kingdoms.discord.admin_panel_mods import registered_admin_mod_sections
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.service import KingdomsService
from tests.mocks.discord_mock import MockInteraction, MockMember

from ..test_mods.test_kingdoms_service import MemoryStore


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    yield
    panel.unregister_kingdoms_admin_section()


def _interaction(client: Any = None) -> MockInteraction:
    return MockInteraction(user=MockMember(), client=client, locale="fr")


def _client(store: MemoryStore) -> SimpleNamespace:
    """A fake bot client exposing the real service pair over MemoryStore."""
    kingdoms = KingdomsService(store, default_season_config())  # type: ignore[arg-type]
    return SimpleNamespace(kingdoms_service=kingdoms)


async def _launched_store() -> MemoryStore:
    store = MemoryStore()
    kingdoms = KingdomsService(store, default_season_config())  # type: ignore[arg-type]
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    return store


def _walk(view: discord.ui.LayoutView) -> list[Any]:
    """Flatten a Components V2 view into its items."""
    items: list[Any] = []

    def _visit(node: Any) -> None:
        for child in getattr(node, "children", ()) or ():
            items.append(child)
            _visit(child)

    _visit(view)
    return items


def _fill(modal: panel.AdminReasonModal, **values: str) -> None:
    """Set modal field values through the internal payload (repo idiom)."""
    for key, value in values.items():
        object.__setattr__(modal._fields[key], "_value", value)


def _custom_ids(view: discord.ui.LayoutView) -> list[str]:
    return [item.custom_id for item in _walk(view) if getattr(item, "custom_id", None) is not None]


def test_section_registration() -> None:
    """The Royaume section registers under the kingdoms mod key."""
    client = SimpleNamespace(add_dynamic_items=lambda *classes: None)
    panel.register_kingdoms_admin_section(client)  # type: ignore[arg-type]
    sections = registered_admin_mod_sections()
    assert [s.mod for s in sections] == ["kingdoms"]
    assert sections[0].label == "🏰 Royaume"
    assert sections[0].description
    panel.unregister_kingdoms_admin_section()
    assert registered_admin_mod_sections() == ()


async def test_entry_degrades_without_service() -> None:
    """No wired service -> the view says so, with no action components."""
    view = await panel.section_entry(_interaction(None))
    assert _custom_ids(view) == []
    text = " ".join(str(getattr(item, "content", "")) for item in _walk(view))
    assert "inactif" in text


async def test_entry_builds_the_season_controls() -> None:
    """A launched season -> the recruitment select and the three buttons."""
    store = await _launched_store()
    view = await panel.section_entry(_interaction(_client(store)))
    ids = _custom_ids(view)
    assert "admin:pin:mod:kingdoms:recruitment" in ids
    assert "admin:pin:mod:kingdoms:quotas" in ids
    assert "admin:pin:mod:kingdoms:foundation" in ids
    applications = [i for i in ids if i.startswith("admin:pin:mod:kingdoms:applications")]
    assert applications == ["admin:pin:mod:kingdoms:applications-close"]


async def test_recruitment_flips_through_the_modal() -> None:
    """A recruitment flip runs through the reason modal into the journal."""
    store = await _launched_store()
    client = _client(store)
    interaction = _interaction(client)
    view = await panel.section_entry(interaction)
    select = next(
        item
        for item in _walk(view)
        if getattr(item, "custom_id", None) == "admin:pin:mod:kingdoms:recruitment"
    )
    assert isinstance(select, panel.KingdomsAdminRecruitmentSelect)
    object.__setattr__(select.item, "_values", ["Aquitaine"])
    await select.callback(interaction)
    modal = interaction.response.modal
    assert isinstance(modal, panel.AdminReasonModal)
    _fill(modal, reason="ouvre le recrutement")
    await modal.on_submit(interaction)
    kingdoms = await client.kingdoms_service.kingdoms()
    aquitaine = next(k for k in kingdoms if k.name == "Aquitaine")
    assert aquitaine.recruitment_open is True
    [action] = list(store.admin_actions.values())
    assert action["action_type"] == "set_recruitment"
    assert action["reason"] == "ouvre le recrutement"


async def test_applications_button_sends_the_modal() -> None:
    """The applications button decodes its wire action and opens the modal."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    button = panel.KingdomsAdminActionButton(
        "applications-close", "Candidatures : ouvertes", discord.ButtonStyle.success
    )
    await button.callback(interaction)
    modal = interaction.response.modal
    assert isinstance(modal, panel.AdminReasonModal)
    _fill(modal, reason="ferme les candidatures")
    await modal.on_submit(interaction)
    season = await _client(store).kingdoms_service.current_season()
    assert season is not None
    assert season.applications_open is False


async def test_quotas_parse_and_reset() -> None:
    """Quota fields: numbers set the override, blanks reset to None."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    modal = panel.AdminReasonModal(interaction, "quotas", {})
    _fill(modal, reason="élargit la saison", kingdoms_count="6", lords_per_kingdom="")
    await modal.on_submit(interaction)
    season = await _client(store).kingdoms_service.current_season()
    assert season is not None
    assert season.kingdoms_count_override == 6
    assert season.lords_per_kingdom_override is None


async def test_quotas_reject_garbage() -> None:
    """A non-numeric quota answers the error string and changes nothing."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    modal = panel.AdminReasonModal(interaction, "quotas", {})
    _fill(modal, reason="réglage", kingdoms_count="six")
    await modal.on_submit(interaction)
    assert "entiers" in str(interaction.response.message.content)
    assert store.admin_actions == {}


async def test_foundation_parse() -> None:
    """Foundation checkboxes: 1/0 set, blank leaves unchanged (None)."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    modal = panel.AdminReasonModal(interaction, "foundation", {})
    _fill(modal, reason="ouvre la fondation aux rois", king="1", admin="")
    await modal.on_submit(interaction)
    season = await _client(store).kingdoms_service.current_season()
    assert season is not None
    assert season.foundation_king is True
    assert season.foundation_admin is None


async def test_reason_gate_answers_when_stripped_empty() -> None:
    """A whitespace-only reason never reaches the store (D75 gate on the wire)."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    modal = panel.AdminReasonModal(interaction, "applications", {"open": True})
    _fill(modal, reason="   ")
    await modal.on_submit(interaction)
    assert store.admin_actions == {}
    assert "Motif" in str(interaction.response.message.content)
