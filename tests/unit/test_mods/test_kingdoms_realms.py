"""Kingdoms realm validation panel & per-kingdom structure — unit tests.

The Royaumes panel and the ``Royaume [Nom]`` structure behind the admin
validation (rule 35, D70): pending kingdoms show validate/refuse/rename
buttons, the structure is idempotent, and refused kingdoms lose theirs.
"""
from __future__ import annotations

import discord
import pytest

from kingdoms.mods.kingdoms.kingdom_realms import (
    REALM_SALONS,
    build_realms_panel,
    delete_realm_structure,
    ensure_realm_structure,
    realm_category_name,
)
from tests.mocks.discord_mock import MockCategoryChannel, MockGuild

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


def test_panel_buttons_target_pending_and_refused_only() -> None:
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
        "kingdoms:realm:approve:k-3",
        "kingdoms:realm:refuse:k-3",
        "kingdoms:realm:rename:k-3",
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
