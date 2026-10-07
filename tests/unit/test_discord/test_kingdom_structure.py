"""Unit tests for the per-kingdom category provisioning (D70, tranche ①)."""

from __future__ import annotations

import pytest

from kingdoms.discord.kingdom_setup import _slug
from kingdoms.discord.kingdom_structure import (
    KINGDOM_CHANNELS,
    ensure_kingdom_structure,
    kingdom_category_name,
)
from tests.mocks.discord_mock import MockGuild, MockMember


class _FakeLord:
    """A lords() item double with the LordModel shape."""

    def __init__(self, player_id: str, kingdom_id: str, *, left: bool = False) -> None:
        self.id = player_id
        self.kingdom_id = kingdom_id
        self.left = left


class _FakeKingdomsService:
    """A kingdoms service double answering lords() from a list."""

    def __init__(self, lords: list[_FakeLord]) -> None:
        self._lords = lords

    async def lords(self) -> list[_FakeLord]:
        return self._lords


def _guild_with_members() -> tuple[MockGuild, list[str]]:
    guild = MockGuild()
    ids: list[str] = []
    for name in ("roi-aquitaine", "seigneur-1", "seigneur-2"):
        member = MockMember(name=name, guild=guild)
        guild.add_member(member)
        ids.append(str(member.id))
    return guild, ids


def _category_names(guild: MockGuild) -> list[str]:
    return [category.name for category in guild.categories]


async def test_creates_the_kingdom_category_with_its_eight_channels() -> None:
    guild, ids = _guild_with_members()
    service = _FakeKingdomsService(
        [
            _FakeLord(ids[0], "k-1"),
            _FakeLord(ids[1], "k-1"),
            _FakeLord(ids[2], "k-1", left=True),
        ]
    )
    resolved = await ensure_kingdom_structure(guild, service, "k-1", "Aquitaine")
    assert _slug(kingdom_category_name("Aquitaine")) in _category_names(guild)
    assert len(resolved) == len(KINGDOM_CHANNELS) == 8
    category = next(c for c in guild.categories if c.name == _slug("Royaume Aquitaine"))
    # the salon names carry the D70 emoji display names (slugged by Discord)
    assert [channel.name for channel in category.channels] == [
        _slug(channel_def.display_name) for channel_def in KINGDOM_CHANNELS
    ]


async def test_rerun_is_idempotent_no_duplicate() -> None:
    guild, ids = _guild_with_members()
    service = _FakeKingdomsService([_FakeLord(ids[0], "k-1")])
    await ensure_kingdom_structure(guild, service, "k-1", "Aquitaine")
    channels_before = [channel.id for channel in guild.text_channels]
    await ensure_kingdom_structure(guild, service, "k-1", "Aquitaine")
    channels_after = [channel.id for channel in guild.text_channels]
    assert channels_before == channels_after
    assert _category_names(guild).count(_slug("Royaume Aquitaine")) == 1


async def test_gaia_never_gets_a_kingdom_category() -> None:
    guild, ids = _guild_with_members()
    service = _FakeKingdomsService([_FakeLord(ids[0], "k-gaia")])
    resolved = await ensure_kingdom_structure(guild, service, "k-gaia", "Gaïa")
    assert resolved == []
    assert _category_names(guild) == []


async def test_refuses_a_kingdom_named_like_the_gaia_category() -> None:
    guild, ids = _guild_with_members()
    service = _FakeKingdomsService([_FakeLord(ids[0], "k-1")])
    resolved = await ensure_kingdom_structure(guild, service, "k-1", "Gaïa")
    # the category name would collide with the static Gaïa group — refused
    assert resolved == []
    assert _category_names(guild) == []


async def test_departed_lords_do_not_get_visibility() -> None:
    guild, ids = _guild_with_members()
    service = _FakeKingdomsService([_FakeLord(ids[1], "k-1", left=True), _FakeLord(ids[0], "k-1")])
    await ensure_kingdom_structure(guild, service, "k-1", "Aquitaine")
    category = next(c for c in guild.categories if c.name == _slug("Royaume Aquitaine"))
    member_ids = {str(getattr(target, "id", "")) for target in category._overwrites}
    assert ids[0] in member_ids
    assert ids[1] not in member_ids


@pytest.mark.parametrize("kingdom_name", ["", "  "])
async def test_blank_names_are_refused(kingdom_name: str) -> None:
    guild, _ids = _guild_with_members()
    service = _FakeKingdomsService([])
    resolved = await ensure_kingdom_structure(guild, service, "k-1", kingdom_name)
    assert resolved == []
    assert _category_names(guild) == []
