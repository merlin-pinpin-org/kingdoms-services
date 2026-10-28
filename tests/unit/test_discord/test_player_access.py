"""Player gating & read-only policy bulk overwrites (livelock 24/10).

``apply_player_gating`` re-runs on EVERY live-dashboard refresh
(15 s) and ``apply_read_only_policy`` on every policy resolution —
per-target ``set_permissions`` calls rate-limit-stormed Discord and
froze the bot's whole HTTP layer (interactions acked too late).
Both now converge with ONE ``channel.edit(overwrites=...)`` and make
ZERO API writes when the live channel already matches.
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.player_access import (
    ADMIN_ROLE_NAMES,
    PLAYER_ROLE_NAMES,
    apply_player_gating,
)
from kingdoms.discord.pinned_views import apply_read_only_policy
from tests.mocks.discord_mock import MockGuild, MockRole, MockTextChannel


class _CountingChannel(MockTextChannel):
    """A text channel counting ``edit`` calls (API write proxy)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.edit_calls = 0

    async def edit(self, **kwargs: Any) -> None:
        self.edit_calls += 1
        await super().edit(**kwargs)


@pytest.fixture()
def stack() -> tuple[MockGuild, _CountingChannel]:
    guild = MockGuild()
    channel = _CountingChannel(id=7, name="live-dashboard", guild=guild)
    guild._channels[channel.id] = channel
    for name in PLAYER_ROLE_NAMES:
        role = MockRole(name=name)
        guild._roles[role.id] = role
    return guild, channel


@pytest.mark.asyncio
async def test_player_gating_single_edit(stack: tuple[MockGuild, _CountingChannel]) -> None:
    guild, channel = stack

    await apply_player_gating(guild, channel)

    assert channel.edit_calls == 1  # ONE bulk write, never one PUT per target
    overwrites = channel.overwrites
    assert overwrites[guild.default_role].view_channel is False
    assert overwrites[guild.me].send_messages is True  # the bot keeps its rights
    for name in PLAYER_ROLE_NAMES:
        role = next(r for r in guild.roles if r.name == name)
        assert overwrites[role].view_channel is True


@pytest.mark.asyncio
async def test_player_gating_refresh_is_free(stack: tuple[MockGuild, _CountingChannel]) -> None:
    guild, channel = stack

    await apply_player_gating(guild, channel)
    await apply_player_gating(guild, channel)  # the 15 s refresh pass

    assert channel.edit_calls == 1  # converged — zero API write on refresh


@pytest.mark.asyncio
async def test_player_gating_preserves_foreign_overwrites(
    stack: tuple[MockGuild, _CountingChannel],
) -> None:
    guild, channel = stack
    foreign = MockRole(name="Diplomate")
    guild._roles[foreign.id] = foreign
    channel._overwrites[foreign] = discord.PermissionOverwrite(view_channel=True)

    await apply_player_gating(guild, channel)

    assert channel.overwrites[foreign].view_channel is True  # untouched


@pytest.mark.asyncio
async def test_read_only_policy_single_edit_and_idempotent(
    stack: tuple[MockGuild, _CountingChannel],
) -> None:
    guild, channel = stack

    await apply_read_only_policy(guild, channel, "home", str(guild.id), settings_db=None)

    assert channel.edit_calls == 1
    assert channel.overwrites[guild.default_role].send_messages is False  # read-only for @everyone
    assert channel.overwrites[guild.me].send_messages is True  # the pin stays refreshable

    await apply_read_only_policy(guild, channel, "home", str(guild.id), settings_db=None)

    assert channel.edit_calls == 1  # converged — zero API write on re-pass


@pytest.mark.asyncio
async def test_home_admin_gating_drops_stale_player_roles(
    stack: tuple[MockGuild, _CountingChannel],
) -> None:
    """home-admin-v1: the home channel is admin-only — the Lord/King
    overwrites left by the previous policy are DROPPED, not ignored."""
    guild, channel = stack

    await apply_player_gating(guild, channel)  # the old player policy first
    assert channel.edit_calls == 1

    await apply_player_gating(guild, channel, ADMIN_ROLE_NAMES)

    assert channel.edit_calls == 2  # one more bulk write
    overwrites = channel.overwrites
    assert overwrites[guild.default_role].view_channel is False
    admin = next(r for r in guild.roles if r.name == "Kingdoms Admin")
    assert overwrites[admin].view_channel is True
    for name in ("Kingdoms Lord", "Kingdoms King"):
        role = next(r for r in guild.roles if r.name == name)
        assert role not in overwrites  # the stale player allow is gone

    await apply_player_gating(guild, channel, ADMIN_ROLE_NAMES)  # the re-pass

    assert channel.edit_calls == 2  # converged — zero API write


@pytest.mark.asyncio
async def test_home_admin_gating_preserves_foreign_overwrites(
    stack: tuple[MockGuild, _CountingChannel],
) -> None:
    guild, channel = stack
    foreign = MockRole(name="Diplomate")
    guild._roles[foreign.id] = foreign
    channel._overwrites[foreign] = discord.PermissionOverwrite(view_channel=True)

    await apply_player_gating(guild, channel, ADMIN_ROLE_NAMES)

    assert channel.overwrites[foreign].view_channel is True  # untouched
