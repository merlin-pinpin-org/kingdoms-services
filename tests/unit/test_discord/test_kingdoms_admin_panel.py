"""Unit tests for the Royaume admin panel section (kingdoms#214 tranches ②-a/②-b).

The section registers into the pinned panel's mod seam, builds its
Components V2 view from the live season, and routes every action
through the D75 reason modal — the socle re-checks the reason, so
these tests prove the wiring: the namespace ids, the unwired
degradation, the view composition and one full run of each season
operation (recruitment, applications, quotas, foundation) and each
roster operation (add_lord, assign_queued, reassign, eject, throne
swap) through the real KingdomAdminService over the in-memory store.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import discord
import pytest

import kingdoms.discord.kingdoms_admin_panel as panel
from kingdoms.discord.admin_panel_mods import registered_admin_mod_sections
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.service import KING_ROLE, LORD_ROLE, KingdomsService
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
    _fill(modal, reason="ferme le recrutement")
    await modal.on_submit(interaction)
    kingdoms = await client.kingdoms_service.kingdoms()
    aquitaine = next(k for k in kingdoms if k.name == "Aquitaine")
    assert aquitaine.recruitment_open is False  # the launch default True flipped
    [action] = list(store.admin_actions.values())
    assert action["action_type"] == "set_recruitment"
    assert action["reason"] == "ferme le recrutement"


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
    assert season.foundation_admin is True  # the launch default, unchanged by the blank


async def test_reason_gate_answers_when_stripped_empty() -> None:
    """A whitespace-only reason never reaches the store (D75 gate on the wire)."""
    store = await _launched_store()
    interaction = _interaction(_client(store))
    modal = panel.AdminReasonModal(interaction, "applications", {"open": True})
    _fill(modal, reason="   ")
    await modal.on_submit(interaction)
    assert store.admin_actions == {}
    assert "Motif" in str(interaction.response.message.content)


async def _roster_run(store: MemoryStore, action: str, **fields: str) -> Any:
    """Drive one roster button through its modal, like a real admin."""
    interaction = _interaction(_client(store))
    button = panel.KingdomsAdminRosterButton(action, action, discord.ButtonStyle.primary)
    await button.callback(interaction)
    modal = interaction.response.modal
    assert isinstance(modal, panel.AdminReasonModal)
    values = dict(fields)
    _fill(modal, reason=values.pop("reason"), **values)
    await modal.on_submit(interaction)
    return interaction


def _action_of(store: MemoryStore, action_type: str) -> dict[str, Any]:
    """The journaled action of the given type on this store."""
    return next(a for a in store.admin_actions.values() if a["action_type"] == action_type)


async def test_entry_builds_the_roster_buttons() -> None:
    """The five roster buttons ride the section view."""
    store = await _launched_store()
    view = await panel.section_entry(_interaction(_client(store)))
    ids = _custom_ids(view)
    for action in ("add-lord", "assign-queued", "reassign", "eject", "swap-throne"):
        assert f"admin:pin:mod:kingdoms:roster-{action}" in ids


async def test_add_lord_runs_through_the_modal() -> None:
    """The add-lord button adds a lord to a kingdom, journaled."""
    store = await _launched_store()
    await _roster_run(
        store, "add-lord", reason="renfort manuel", player_id="p1", display_name="P1", role="lord", kingdom="Aquitaine"
    )
    service = _client(store).kingdoms_service
    lord = next(lord for lord in await service.lords() if lord.id == "p1")
    aquitaine = next(k for k in await service.kingdoms() if k.name == "Aquitaine")
    assert lord.kingdom_id == aquitaine.id
    assert lord.role is LORD_ROLE
    assert _action_of(store, "add_lord")["action_type"] == "add_lord"


async def test_assign_queued_runs_through_the_modal() -> None:
    """A queued player is assigned to a kingdom, journaled."""
    store = await _launched_store()
    service = _client(store).kingdoms_service
    await service.enroll("q1", "Q1", LORD_ROLE)
    await _roster_run(
        store, "assign-queued", reason="sortie de file", player_id="q1", kingdom="Bourgogne", role="lord"
    )
    lord = next(lord for lord in await service.lords() if lord.id == "q1")
    bourgogne = next(k for k in await service.kingdoms() if k.name == "Bourgogne")
    assert lord.in_queue is False
    assert lord.kingdom_id == bourgogne.id
    assert _action_of(store, "assign_queued")["action_type"] == "assign_queued"


async def test_reassign_moves_an_active_lord() -> None:
    """Reassignment moves an enrolled lord to another kingdom."""
    store = await _launched_store()
    service = _client(store).kingdoms_service
    await service.enroll("m1", "M1", LORD_ROLE, kingdom_name="Aquitaine")
    await _roster_run(store, "reassign", reason="équilibrage", player_id="m1", kingdom="Bourgogne")
    lord = next(lord for lord in await service.lords() if lord.id == "m1")
    bourgogne = next(k for k in await service.kingdoms() if k.name == "Bourgogne")
    assert lord.kingdom_id == bourgogne.id
    assert _action_of(store, "reassign")["action_type"] == "reassign"


async def test_eject_returns_a_lord_to_the_queue() -> None:
    """The eject button queues an active lord back, role kept."""
    store = await _launched_store()
    service = _client(store).kingdoms_service
    await service.enroll("m1", "M1", LORD_ROLE, kingdom_name="Aquitaine")
    await _roster_run(store, "eject", reason="sanction", player_id="m1")
    lord = next(lord for lord in await service.lords() if lord.id == "m1")
    assert lord.in_queue is True
    assert lord.kingdom_id is None
    assert _action_of(store, "eject_to_queue")["action_type"] == "eject_to_queue"


async def test_swap_throne_promotes_a_lord() -> None:
    """The throne swap demotes the King and crowns the chosen lord."""
    store = await _launched_store()
    await _roster_run(
        store, "add-lord", reason="roi initial", player_id="k1", display_name="K1", role="king", kingdom="Aquitaine"
    )
    await _roster_run(
        store, "add-lord", reason="prétendant", player_id="k2", display_name="K2", role="lord", kingdom="Aquitaine"
    )
    await _roster_run(store, "swap-throne", reason="abdication", kingdom="Aquitaine", new_king_id="k2")
    service = _client(store).kingdoms_service
    lords = {lord.id: lord for lord in await service.lords()}
    assert lords["k2"].role is KING_ROLE
    assert lords["k1"].role is LORD_ROLE
    assert _action_of(store, "swap_throne")["action_type"] == "swap_throne"


async def test_roster_rejects_a_bad_role() -> None:
    """A role outside king/lord answers the error and journals nothing."""
    store = await _launched_store()
    interaction = await _roster_run(
        store, "add-lord", reason="essai", player_id="p1", display_name="P1", role="boss", kingdom="Aquitaine"
    )
    assert "king ou lord" in str(interaction.response.message.content)
    assert store.admin_actions == {}


async def test_roster_requires_the_fields() -> None:
    """A blank required roster field never reaches the store."""
    store = await _launched_store()
    interaction = await _roster_run(store, "eject", reason="essai", player_id="")
    assert "champs requis" in str(interaction.response.message.content)
    assert store.admin_actions == {}
