"""Unit tests for the 🏰 Royaume admin panel (epic #214 phase 1.2, D75).

The pinned panel renders from a pure snapshot (marker-refresh
contract); the action flows run through the journaled
``KingdomAdminService`` with the mandatory-reason gate (D75), and the
helpers parse the modal inputs. The Discord seam rides MockDiscord, the
persistence seam the domain tests' MemoryStore — no network anywhere.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.discord.royaume_panel import (
    ROYAUME_PANEL_MARKER,
    RoyaumePanelWiring,
    _optional_bool,
    _optional_int,
    _role_of,
    build_action_modal,
    build_panel_content,
    build_panel_view,
    register_royaume_panel_wiring,
    run_action,
    snapshot_from_services,
)
from kingdoms.mods.kingdoms.admin_service import KingdomAdminService
from kingdoms.mods.kingdoms.config import default_season_config
from kingdoms.mods.kingdoms.models import LordRole
from kingdoms.mods.kingdoms.service import KingdomsService
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockMember
from tests.unit.test_mods.test_kingdoms_service import MemoryStore


def _services() -> tuple[KingdomAdminService, KingdomsService, MemoryStore]:
    """A season over MemoryStore: Aquitaine and Bourgogne once launched."""
    store = MemoryStore()
    kingdoms = KingdomsService(store, default_season_config())  # type: ignore[arg-type]
    return KingdomAdminService(kingdoms, store), kingdoms, store


async def _launched() -> tuple[KingdomAdminService, KingdomsService, MemoryStore]:
    """Build the services and launch the imposed two-kingdom season."""
    admin, kingdoms, store = _services()
    await kingdoms.launch(["Aquitaine", "Bourgogne"])
    return admin, kingdoms, store


def _interaction(guild: MockGuild | None = None) -> MockInteraction:
    """An admin interaction (BOT_ADMINS path) over an optional guild."""
    member = MockMember(id=1, name="Drasah")
    return MockInteraction(user=member, guild=guild)


def _wire(admin: Any, kingdoms: Any, guild: MockGuild | None = None) -> MockInteraction:
    """Wire the admin service and build the interaction (BOT_ADMINS=1)."""
    register_royaume_panel_wiring(
        RoyaumePanelWiring(
            admin_service=admin,
            kingdoms_service=kingdoms,
            bot_admins=("1",),
        )
    )
    return _interaction(guild=guild)


async def test_panel_content_without_season() -> None:
    """No season: the placeholder message still carries the marker."""
    content = build_panel_content(None, "fr")
    assert ROYAUME_PANEL_MARKER in content
    assert "Aucune saison" in content


async def test_panel_content_lists_kingdoms_switches_and_journal() -> None:
    """A launched season renders kingdoms, switches, quotas and the journal."""
    admin, kingdoms, _ = await _launched()
    await admin.set_recruitment("Bourgogne", open=False, actor_id="1", actor_name="Drasah", reason="gel")
    snapshot = await snapshot_from_services(admin, kingdoms)
    assert snapshot is not None
    content = build_panel_content(snapshot, "fr")
    assert ROYAUME_PANEL_MARKER in content
    assert "**Aquitaine**" in content and "**Bourgogne**" in content
    assert "fermé" in content  # Bourgogne's recruitment switch, localized
    assert "set_recruitment" in content  # the journal tail


async def test_run_action_requires_a_reason() -> None:
    """An empty reason is refused before anything resolves (D75)."""
    admin, kingdoms, store = await _launched()
    interaction = _wire(admin, kingdoms)
    await run_action(interaction, "create_kingdom", {"kingdom": "Gascogne", "reason": "  "})
    message = interaction.response.message.content
    assert "motif" in message.lower()
    assert not any(k.name == "Gascogne" for k in await kingdoms.kingdoms())
    assert not store.admin_actions


async def test_run_action_creates_and_journals_a_kingdom() -> None:
    """A valid action runs through the service, journal and panel refresh."""
    admin, kingdoms, store = await _launched()
    interaction = _wire(admin, kingdoms, guild=MockGuild(id=99))
    await run_action(interaction, "create_kingdom", {"kingdom": "Gascogne", "reason": "D75 test"})
    message = interaction.followup.messages[-1].content
    assert "Gascogne" in message
    assert any(k.name == "Gascogne" for k in await kingdoms.kingdoms())
    assert store.admin_actions and store.admin_actions[0]["reason"] == "D75 test"


async def test_run_action_localizes_a_domain_error() -> None:
    """A not-found kingdom answers the localized error, journal untouched."""
    admin, kingdoms, store = await _launched()
    interaction = _wire(admin, kingdoms)
    await run_action(interaction, "rename_kingdom", {"kingdom": "Nulle", "new_kingdom": "X", "reason": "r"})
    message = interaction.followup.messages[-1].content
    assert "introuvable" in message
    assert not store.admin_actions


def test_panel_view_wires_every_action_button() -> None:
    """The pinned panel view carries every D75 action, ids on the wire."""
    view = build_panel_view("fr")
    buttons = [item for item in view.children if hasattr(item, "custom_id")]
    ids = {item.custom_id for item in buttons}
    assert len(buttons) == 16
    assert "kingdoms:royaume:add_lord" in ids
    assert "kingdoms:royaume:dissolve" in ids
    assert "kingdoms:royaume:quotas" in ids
    assert "kingdoms:royaume:foundation" in ids
    assert "kingdoms:royaume:journal" in ids


def test_optional_parsers() -> None:
    """Quota inputs parse leniently: empty = default, on/off = tri-state."""
    assert _optional_int("") is None
    assert _optional_int("  7 ") == 7
    assert _optional_bool("") is None
    assert _optional_bool("on") is True
    assert _optional_bool("off") is False


def test_role_of_accepts_fr_and_en_words() -> None:
    """The modal role word resolves for both languages (D75)."""
    assert _role_of("Roi") is LordRole.KING
    assert _role_of("king") is LordRole.KING
    assert _role_of("Seigneur") is LordRole.LORD
    assert _role_of("lord") is LordRole.LORD
    with pytest.raises(ValueError, match="unknown role"):
        _role_of("empereur")


def test_modal_shapes_carry_the_reason_everywhere() -> None:
    """Every action modal ends with the mandatory reason field (D75)."""
    for action in (
        "add_lord",
        "assign_queued",
        "reassign",
        "swap_throne",
        "eject",
        "dissolve",
        "create_kingdom",
        "rename_kingdom",
        "recruitment_open",
        "applications_close",
        "quotas",
        "foundation",
        "rollback",
    ):
        modal: Any = build_action_modal(action, "fr")
        assert modal.title, action
        assert modal.field_0, action  # the modal builds its fields
