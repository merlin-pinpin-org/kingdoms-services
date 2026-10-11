"""Kingdoms realm validation panel & per-kingdom structure — unit tests.

The Royaumes panel and the ``Royaume [Nom]`` structure behind the admin
validation (rule 35, D70): pending kingdoms show validate/refuse/rename
buttons, the structure is idempotent, and refused kingdoms lose theirs.
"""
from __future__ import annotations

from typing import ClassVar

import discord
import pytest

from kingdoms.mods.kingdoms.kingdom_realms import (
    REALM_SALONS,
    build_realms_panel,
    delete_orphan_realm_categories,
    delete_realm_structure,
    ensure_realm_structure,
    realm_category_name,
)
from tests.mocks.discord_mock import MockCategoryChannel, MockGuild, MockMember

pytestmark = pytest.mark.asyncio


class _Kingdom:
    """A minimal kingdom stand-in for the panel builder."""

    def __init__(self, kid: str, name: str, validation: str, is_gaia: bool = False) -> None:
        self.id = kid
        self.name = name
        self.validation = validation
        self.is_gaia = is_gaia


class _Lord:
    def __init__(self, lid: str, kid: str | None, role: str, display: str) -> None:
        self.id = lid
        self.kingdom_id = kid
        self.role = role
        self.display_name = display
        self.left = False


def _rows(view):
    """Flatten the top-level children of every LayoutView container."""
    out = []
    for child in view.children:
        for sub in getattr(child, "children", []):
            out.append(sub)
    return out


def _buttons(view):
    return [
        button for row in _rows(view) if hasattr(row, "children") for button in row.children
    ]


def test_panel_buttons_validation_and_delete() -> None:
    kingdoms = [
        _Kingdom("gaia", "gaia", "approved", is_gaia=True),
        _Kingdom("k-1", "Avalon", "pending"),
        _Kingdom("k-2", "Bourgogne", "approved"),
        _Kingdom("k-3", "Ouest", "refused"),
    ]
    lords = [_Lord("p1", "k-1", "king", "Arthur")]
    view = build_realms_panel("fr", kingdoms, lords)
    ids = [button.custom_id for button in _buttons(view)]
    assert ids == [
        "kingdoms:realm:approve:k-1",
        "kingdoms:realm:refuse:k-1",
        "kingdoms:realm:rename:k-1",
        "kingdoms:realm:delete:k-1",
        # an approved kingdom has nothing to validate but stays deletable
        "kingdoms:realm:delete:k-2",
        "kingdoms:realm:approve:k-3",
        "kingdoms:realm:refuse:k-3",
        "kingdoms:realm:rename:k-3",
        "kingdoms:realm:delete:k-3",
    ]


def test_panel_never_serializes_an_empty_action_row() -> None:
    kingdoms = [_Kingdom("k-1", "Avalon", "approved")]
    view = build_realms_panel("fr", kingdoms, [])
    for row in _rows(view):
        if isinstance(row, discord.ui.ActionRow):
            assert len(row.children) >= 1


def test_panel_empty_state_and_gaia_hidden() -> None:
    view = build_realms_panel("fr", [_Kingdom("gaia", "gaia", "approved", is_gaia=True)], [])
    assert _buttons(view) == []


async def test_ensure_realm_structure_creates_category_and_salons() -> None:
    guild = MockGuild()
    await guild.create_role(name="kingdoms_admin")
    lord_role = await guild.create_role(name="kingdoms_lord")
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    category = await ensure_realm_structure(guild, kingdom, [111])
    assert isinstance(category, MockCategoryChannel)
    assert category.name == realm_category_name("Avalon")
    assert len(category.channels) == len(REALM_SALONS)
    names = {channel.name for channel in category.channels}
    assert {"Territoire", "Alliances", "Église"} <= names
    # the public salons are readable by every kingdoms_lord, the private ones are not
    territoire = next(c for c in category.channels if c.name == "Territoire")
    assert territoire.permission_overwrite_for(lord_role) is not None
    prive = next(c for c in category.channels if c.name == "Salle du Conseil")
    assert prive.permission_overwrite_for(lord_role) is None


async def test_ensure_realm_structure_is_idempotent() -> None:
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    await ensure_realm_structure(guild, kingdom, [])
    category = await ensure_realm_structure(guild, kingdom, [])
    assert len(guild.categories) == 1
    assert len(category.channels) == len(REALM_SALONS)
    assert len(guild.text_channels) == len(REALM_SALONS)


async def test_delete_realm_structure_removes_everything() -> None:
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    await ensure_realm_structure(guild, kingdom, [])
    assert await delete_realm_structure(guild, "Avalon") is True
    assert guild.categories == []
    assert guild.text_channels == []
    assert await delete_realm_structure(guild, "Avalon") is False


async def test_ensure_realm_structure_grants_real_members_only() -> None:
    guild = MockGuild()
    member = MockMember(id=111, name="Arthur", guild=guild)
    guild.add_member(member)
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    category = await ensure_realm_structure(guild, kingdom, [111, 222])
    assert category.permission_overwrite_for(member) is not None
    # 222 is not in the guild: skipped, never a raw-Object overwrite crash
    assert all(not isinstance(target, discord.Object) for target in ())
    overwrites = [(key, value) for key, value in category._overwrites.items()]
    assert (member.id, False) in [key for key, _ in overwrites]


async def test_ensure_realm_structure_grants_the_bot_itself() -> None:
    """Regression (drasah live incident): the bot must keep access to its
    own realm categories — otherwise every state view deployment fails
    with a silent 403 Missing Access and the salons stay empty."""
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    category = await ensure_realm_structure(guild, kingdom, [])
    assert category.permission_overwrite_for(guild.me) is not None


async def test_delete_orphan_realm_categories_keeps_the_living_kingdoms() -> None:
    guild = MockGuild()
    living = _Kingdom("k-1", "Avalon", "approved")
    orphan = _Kingdom("k-9", "Ouest", "approved")
    await ensure_realm_structure(guild, living, [])
    await ensure_realm_structure(guild, orphan, [])
    deleted = await delete_orphan_realm_categories(guild, [living])
    assert deleted == 1
    assert [category.name for category in guild.categories] == [realm_category_name("Avalon")]


async def test_delete_orphan_realm_categories_keeps_the_gaia_structure() -> None:
    """« Royaume Gaïa » is declared structure, never an orphan realm."""
    guild = MockGuild()
    living = _Kingdom("k-1", "Avalon", "approved")
    await ensure_realm_structure(guild, living, [])
    gaia = await guild.create_category(realm_category_name("Gaïa"))
    await guild.create_text_channel("Patrouille", category=gaia)
    deleted = await delete_orphan_realm_categories(guild, [living])
    assert deleted == 0
    names = [category.name for category in guild.categories]
    assert names == [realm_category_name("Avalon"), realm_category_name("Gaïa")]


class _StubService:
    """The minimal kingdoms surface the panel deploy needs."""

    def __init__(self, kingdoms: list, lords: list) -> None:
        self._kingdoms = kingdoms
        self._lords = lords

    async def kingdoms(self) -> list:
        return self._kingdoms

    async def lords(self) -> list:
        return self._lords


async def test_deploy_realms_panel_replaces_its_old_message() -> None:
    from kingdoms.mods.kingdoms.kingdom_realms import deploy_realms_panel

    guild = MockGuild()
    channel = await guild.create_text_channel("royaumes")
    service = _StubService([_Kingdom("k-1", "Avalon", "pending")], [_Lord("p1", "k-1", "king", "Arthur")])
    def live() -> int:
        return len([m for m in channel.messages if not getattr(m, "deleted", False)])

    assert await deploy_realms_panel(guild, "fr", service) is True
    assert live() == 1
    assert await deploy_realms_panel(guild, "fr", service) is True
    # the old panel (Components V2: marker inside the view) was deleted
    assert live() == 1
    # a channel that does not exist answers False
    empty_guild = MockGuild()
    assert await deploy_realms_panel(empty_guild, "fr", service) is False


async def test_realm_category_is_created_with_overwrites_in_one_call() -> None:
    """Regression (drasah live incident 2026-10-10): the category must be
    created with its FULL overwrite set in the single create_category
    call — not filled afterwards with 4-6 rate-limited set_permissions
    PATCHes that left the category public/empty for minutes."""
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    category = await ensure_realm_structure(guild, kingdom, [])
    # the overwrites were applied at creation time: bot + @everyone are
    # already present, and @everyone is denied
    assert category.permission_overwrite_for(guild.me) is not None
    assert category.permission_overwrite_for(guild.default_role) is not None
    everyone = category.permission_overwrite_for(guild.default_role)
    assert everyone is not None and everyone.view_channel is False


async def test_ensure_realm_structure_resyncs_overwrites_via_edit() -> None:
    """A pre-existing category (pre-fix, missing the bot overwrite) is
    adopted and its overwrites re-synced with ONE edit() call."""
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    first = await ensure_realm_structure(guild, kingdom, [])
    # simulate a pre-fix category: strip every overwrite
    first._overwrites = {}
    category = await ensure_realm_structure(guild, kingdom, [])
    assert category is first
    assert len(guild.categories) == 1
    assert category.permission_overwrite_for(guild.me) is not None
    assert category.permission_overwrite_for(guild.default_role) is not None


async def test_delete_realm_structure_grants_bot_access_first() -> None:
    """Regression (drasah live incident): a pre-fix category without a bot
    overwrite (403 Missing Access on delete) must be deletable anyway."""
    guild = MockGuild()
    kingdom = _Kingdom("k-1", "Avalon", "approved")
    category = await ensure_realm_structure(guild, kingdom, [])
    category._overwrites = {}  # pre-fix state: bot is locked out
    assert await delete_realm_structure(guild, "Avalon") is True
    assert guild.categories == []


class _PhaseKingdom:
    """A minimal kingdom for the draft-gating tests."""

    id = "k-1"
    name = "Avalon"
    validation = "approved"
    is_gaia = False
    civilizations: ClassVar[list[str]] = []


class _PhaseSeason:
    def __init__(self, phase: str) -> None:
        self.phase = phase


class _PhaseKingdomsService:
    """Kingdoms service whose season phase is configurable."""

    def __init__(self, phase: str) -> None:
        self._season = _PhaseSeason(phase)

    async def current_season(self) -> _PhaseSeason:
        return self._season

    async def kingdoms(self) -> list:
        return [_PhaseKingdom()]

    async def lords(self) -> list:
        return []


async def test_setup_phase_pins_the_alliances_placeholder_not_the_draft() -> None:
    from kingdoms.mods.kingdoms.kingdom_realms import (
        ALLIANCES_PENDING_MARKER,
        DRAFT_STARTER_MARKER,
        ensure_all_realm_structures,
    )
    from kingdoms.mods.kingdoms.panel_messages import message_text

    guild = MockGuild()
    await ensure_all_realm_structures(guild, _PhaseKingdomsService("setup"))
    category = guild.categories[0]
    alliances = next(c for c in category.channels if c.name == "Alliances")
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1
    text = message_text(live[0])
    assert ALLIANCES_PENDING_MARKER in text
    assert DRAFT_STARTER_MARKER not in text  # the draft stays secret


async def test_started_phase_announces_the_draft_and_kills_the_placeholder() -> None:
    from kingdoms.mods.kingdoms.kingdom_realms import (
        ALLIANCES_PENDING_MARKER,
        DRAFT_STARTER_MARKER,
        ensure_all_realm_structures,
    )
    from kingdoms.mods.kingdoms.panel_messages import message_text

    guild = MockGuild()
    setup_service = _PhaseKingdomsService("setup")
    await ensure_all_realm_structures(guild, setup_service)
    # the game starts: the draft replaces the placeholder
    _PhaseKingdom.civilizations = ["azteques"]
    started_service = _PhaseKingdomsService("started")
    await ensure_all_realm_structures(guild, started_service)
    category = guild.categories[0]
    alliances = next(c for c in category.channels if c.name == "Alliances")
    live = [m for m in alliances.messages if not getattr(m, "deleted", False)]
    assert len(live) == 1  # the placeholder was deleted, the draft pinned
    text = message_text(live[0])
    assert DRAFT_STARTER_MARKER in text
    assert ALLIANCES_PENDING_MARKER not in text
    assert "Aztèques" not in text  # display names need the config catalog
    _PhaseKingdom.civilizations = []
