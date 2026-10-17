"""The Paramètres panel — the admin season buttons (phases lot)."""
from __future__ import annotations

from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_panels import SETTINGS_PANEL_MARKER, build_settings_panel

pytestmark = pytest.mark.asyncio


def _custom_ids(view) -> set[str]:
    return {
        getattr(item, "custom_id", "")
        for item in view.walk_children()
        if getattr(item, "custom_id", None)
    }


async def test_settings_panel_carries_the_phase_buttons() -> None:
    view = await build_settings_panel(None, "1", "tester")
    ids = _custom_ids(view)
    assert "kingdoms:admin:start-season" in ids
    assert "kingdoms:admin:start-beta" in ids
    assert "kingdoms:admin:season-mode" in ids
    # the existing actions survive (nothing broken)
    for action in (
        "launch",
        "status",
        "deploy",
        "sync",
        "reset",
        "assign",
        "add-kingdom",
        "remove",
    ):
        assert f"kingdoms:admin:{action}" in ids
    assert SETTINGS_PANEL_MARKER in str(view.to_components())


async def test_settings_panel_rows_never_overflow() -> None:
    """Discord caps an ActionRow at 5 items — an overflowing row makes
    the whole panel send fail."""
    import discord

    view = await build_settings_panel(None, "1", "tester")
    for item in view.walk_children():
        if isinstance(item, discord.ui.ActionRow):
            assert len(item.children) <= 5


async def test_remove_flow_lists_active_lords() -> None:
    """Drasah's rule (2026-10-11): the 🗑️ eject button shows the active
    roster (assigned or waiting) — no handle to type."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _active_lord_options,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings
    from kingdoms.mods.kingdoms.service import KING_ROLE, LORD_ROLE, KingdomsService

    from .test_kingdoms_events import MemoryStore, _config

    service = KingdomsService(MemoryStore(), _config())
    await service.launch([])
    await service.add_kingdom("Aquitaine")
    await service.enroll("king-1", "King Arthur", KING_ROLE, proposed_name="Bretagne")
    await service.enroll("lord-1", "Lord Modal", LORD_ROLE, kingdom_name="Aquitaine")
    await service.enroll("lord-2", "Lord Waiting", LORD_ROLE)  # queue
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))

    strings = _strings("fr")
    options = await _active_lord_options(strings)
    assert options is not None
    labels = {option.value: option for option in options}
    assert set(labels) == {"king-1", "lord-1", "lord-2"}
    assert labels["king-1"].emoji is not None  # the king is crowned
    assert labels["lord-1"].description == "Aquitaine"
    assert labels["lord-2"].description == strings["remove_queue_desc"]


async def test_remove_flow_roster_empty_when_service_gone() -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _active_lord_options,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings

    register_kingdoms_panel_wiring(KingdomsPanelWiring())
    assert await _active_lord_options(_strings("fr")) is None


class _FakeGuildMembers:
    """A guild resolving member ids to display names."""

    def get_member(self, user_id: int) -> Any:
        return _FakeMember(user_id, f"Pseudo {user_id}")


class _FakeMember:
    def __init__(self, id: int, display_name: str) -> None:
        self.id = id
        self.display_name = display_name


async def test_eject_roster_shows_pseudos_not_mentions() -> None:
    """Drasah's rule (2026-10-11): legacy enrollments stored a raw
    ``<@id>`` mention as display_name — the roster select showed a long
    number. The live member lookup must win."""
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _active_lord_options,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings
    from kingdoms.mods.kingdoms.service import KING_ROLE, KingdomsService

    from .test_kingdoms_events import MemoryStore, _config

    king_id = "242421489536729100"
    service = KingdomsService(MemoryStore(), _config())
    await service.launch(["Aquitaine"])
    await service.enroll_king_awaiting_kingdom(king_id, f"<@{king_id}>")
    await service.assign(king_id, "Aquitaine", KING_ROLE)
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))

    strings = _strings("fr")
    with_guild = await _active_lord_options(strings, _FakeGuildMembers())
    assert with_guild is not None
    assert with_guild[0].label == f"Pseudo {king_id}"
    without_guild = await _active_lord_options(strings)
    assert without_guild is not None
    # the mention-shaped legacy name is unreadable — fall back to the id
    assert without_guild[0].label == king_id


async def test_postuler_confirmation_strings_are_distinct() -> None:
    """The confirmation used to print 'sent' twice (profile note = sent)."""
    from kingdoms.mods.kingdoms.kingdom_panels import _strings as panel_strings

    for locale in ("fr", "en"):
        strings = panel_strings(locale)
        assert strings["profile_created"] != strings["sent"]
        assert strings["sent_ttl"]


async def test_ephemeral_confirmation_self_deletes() -> None:
    """The Postuler ephemeral confirmation auto-deletes (~15 min)."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_panels import _schedule_ephemeral_delete

    deleted: list[int] = []

    class _Followup:
        async def delete_message(self, message_id: int) -> None:
            deleted.append(message_id)

    class _Interaction:
        followup = _Followup()

    class _Message:
        id = 42

    _schedule_ephemeral_delete(_Interaction(), _Message(), seconds=0)  # type: ignore[arg-type]
    for _ in range(20):
        await asyncio.sleep(0)
        if deleted:
            break
    await asyncio.sleep(0.05)
    assert deleted == [42]


async def test_start_beta_button_answers_the_beta_panel() -> None:
    """The 🚀 beta button of the Paramètres panel opens the beta start
    panel (« Maintenant » / « Programmer… ») with the live wiring."""
    from kingdoms.mods.kingdoms.beta_panel import BetaStartView
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _run_start_beta,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings

    register_kingdoms_panel_wiring(KingdomsPanelWiring(events_service=object()))
    sent: list[dict[str, Any]] = []

    class _Response:
        async def send_message(self, content: str, **kwargs: Any) -> None:
            sent.append({"content": content, **kwargs})

    class _Interaction:
        locale = None
        response = _Response()

    await _run_start_beta(_Interaction(), _strings("fr"))  # type: ignore[arg-type]
    assert len(sent) == 1
    assert isinstance(sent[0]["view"], BetaStartView)
    assert "beta" in sent[0]["content"].lower()


async def test_start_beta_button_fails_cleanly_without_service() -> None:
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _run_start_beta,
        register_kingdoms_panel_wiring,
    )
    from kingdoms.mods.kingdoms.kingdom_profiles import _strings

    register_kingdoms_panel_wiring(KingdomsPanelWiring())
    sent: list[tuple[str, bool]] = []

    class _Response:
        async def send_message(self, content: str, ephemeral: bool = False) -> None:
            sent.append((content, ephemeral))

    class _Interaction:
        locale = None
        response = _Response()

    await _run_start_beta(_Interaction(), _strings("fr"))  # type: ignore[arg-type]
    assert len(sent) == 1
    assert "échoué" in sent[0][0]
    assert sent[0][1] is True
