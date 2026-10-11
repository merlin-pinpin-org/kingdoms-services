"""Kingdoms salons reset & panel hygiene — unit tests.

Two regression guards of the PR #244 feedback round:

- the reset must purge the per-kingdom ``Royaume …`` categories (they
  are not declared data) and must not sweep the salons living under
  them by name — otherwise the guild ends up with leftovers/duplicates.
- a panel re-deployment must find and remove its previous message on a
  real channel (Components V2: marker inside the view, no ``.messages``
  cache — only ``history()``).
"""
from __future__ import annotations

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    _delete_matching_categories,
    _delete_matching_channels,
)
from kingdoms.mods.kingdoms.kingdom_realms import (
    REALMS_PANEL_MARKER,
    deploy_realms_panel,
)
from kingdoms.mods.kingdoms.panel_messages import channel_messages, message_text
from tests.mocks.discord_mock import MockGuild

pytestmark = pytest.mark.asyncio

STRUCTURE = {"patrouille", "territoire", "seigneurs", "taverne", "conscription"}


async def test_reset_sweep_deletes_realm_salons_too() -> None:
    """Drasah's duplicates incident (2026-10-11): on real Discord a
    deleted category leaves its channels orphaned at the guild root —
    the reinstall then recreates them and the guild fills with
    duplicates. The sweep must therefore delete the realm salons too,
    plus the root-level leftovers of older resets."""
    guild = MockGuild()
    conscription = await guild.create_category("Conscription")
    realm = await guild.create_category("Royaume Avalon")
    await guild.create_text_channel("Patrouille", category=realm)  # realm salon, declared name
    await guild.create_text_channel("Territoire", category=realm)
    await guild.create_text_channel("Patrouille", category=conscription)  # declared salon
    await guild.create_text_channel("Patrouille")  # top-level, declared name
    await guild.create_text_channel("Le-Royaume")  # root-level realm-salon leftover

    deleted = await _delete_matching_channels(guild, STRUCTURE)

    assert deleted == 5  # declared + realm salons + root leftovers
    assert [c.name for c in realm.channels] == []
    assert [c.name for c in conscription.channels] == []


async def test_reset_purges_realm_categories_too() -> None:
    guild = MockGuild()
    await guild.create_category("Conscription")
    realm = await guild.create_category("Royaume Avalon")
    await guild.create_text_channel("Seigneurs", category=realm)
    await guild.create_category("Royaume Gaïa")
    untouched = await guild.create_category("Champs de Bataille")

    deleted = await _delete_matching_categories(guild, {"conscription", "royaume-gaia"})

    # category deletes do NOT cascade on real Discord — the salons die first
    assert [c.name for c in realm.channels] == []
    assert deleted == 4  # declared + realm slice + its salon + declared Gaïa
    assert guild.categories == [untouched]


async def test_channel_messages_falls_back_to_real_history() -> None:
    guild = MockGuild()

    class _RealChannel:
        """A channel without ``.messages`` (like the real API)."""

        def __init__(self) -> None:
            self.name = "saison"

        async def history(self, limit: int):
            class _Msg:
                content = "marker inside content"

            for _ in range(3):
                yield _Msg()

    assert len(await channel_messages(_RealChannel())) == 3
    channel = await guild.create_text_channel("saison")
    await channel.send("hello")
    assert len(await channel_messages(channel)) == 1  # mock cache path


def test_message_text_walks_components_v2_trees() -> None:
    class _Item:
        def __init__(self, text: str, children: list | None = None) -> None:
            self.content = text
            self.children = children or []

    class _Message:
        content = ""
        components = [_Item("title", [_Item("-# kingdoms:panel:royaumes", [])])]  # noqa: RUF012
        layout = None

    assert REALMS_PANEL_MARKER in message_text(_Message())
    assert "marker inside" not in message_text(_Item)  # type: ignore[arg-type]


async def test_realms_panel_redeploy_removes_the_old_marker_message() -> None:
    guild = MockGuild()
    channel = await guild.create_text_channel("royaumes")

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "pending"
        is_gaia = False

    class _Service:
        async def kingdoms(self) -> list:
            return [_Kingdom()]

        async def lords(self) -> list:
            return []

    # a first deploy pins one panel; a second deploy must replace it
    assert await deploy_realms_panel(guild, "fr", _Service()) is True
    first = [m for m in await channel_messages(channel) if not getattr(m, "deleted", False)]
    assert len(first) == 1
    marker_view = first[0].layout is not None
    assert marker_view

    assert await deploy_realms_panel(guild, "fr", _Service()) is True
    live = [m for m in await channel_messages(channel) if not getattr(m, "deleted", False)]
    assert len(live) == 1  # the old one (Components V2 marker) was found and deleted


async def test_draft_starter_pinned_in_alliances_idempotently() -> None:
    from kingdoms.mods.kingdoms.kingdom_realms import (
        DRAFT_STARTER_MARKER,
        announce_draft_starter,
        ensure_realm_structure,
    )

    class _Civ:
        def __init__(self, key: str, display_name: str) -> None:
            self.key = key
            self.display_name = display_name

    class _Config:
        civilizations = (_Civ("azteques", "Aztèques"), _Civ("berberes", "Berbères"))

    class _Service:
        config = _Config()

    class _Kingdom:
        id = "k-1"
        name = "Avalon"
        validation = "approved"
        is_gaia = False
        civilizations = ["azteques", "berberes"]  # noqa: RUF012

    guild = MockGuild()
    await ensure_realm_structure(guild, _Kingdom(), [])
    assert await announce_draft_starter(guild, _Kingdom(), _Service()) is True
    category = guild.categories[0]
    alliances = next(c for c in category.channels if c.name == "Alliances")
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    assert "Draft starter" in live[0].content and "Aztèques" in live[0].content
    assert DRAFT_STARTER_MARKER in live[0].content

    # a second announce edits the marked message, never a duplicate
    assert await announce_draft_starter(guild, _Kingdom(), _Service()) is True
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
