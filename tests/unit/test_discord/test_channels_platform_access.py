"""Access-policy overwrites seam (rate-limit lesson Checks 89/92, 24/10).

``DiscordChannelsPlatform.apply_access_policy`` must apply a channel's
declared policy through ONE ``channel.edit(overwrites=...)`` call and
make zero API writes when the live overwrites already match — the
per-target ``set_permissions`` storm rate-limit-livelocked live
provisioning (taverne 24/10: 429 every ~15 s, interaction token
expired before the followup).
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.discord.channels_platform import DiscordChannelsPlatform
from tests.mocks.discord_mock import MockGuild, MockRole, MockTextChannel


class _CountingChannel(MockTextChannel):
    """A text channel counting ``edit`` calls (API write proxy)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.edit_calls = 0

    async def edit(self, **kwargs: Any) -> None:
        self.edit_calls += 1
        await super().edit(**kwargs)


class _StaticBot:
    """The platform only needs ``get_guild`` here."""

    def __init__(self, guild: MockGuild) -> None:
        self._guild = guild

    def get_guild(self, guild_id: int) -> MockGuild | None:
        return self._guild if self._guild.id == guild_id else None


@pytest.fixture()
def stack() -> tuple[DiscordChannelsPlatform, MockGuild, _CountingChannel, MockRole]:
    guild = MockGuild()
    channel = _CountingChannel(id=42, name="taverne", guild=guild)
    guild._channels[channel.id] = channel
    lord_role = MockRole(name="Kingdoms Lord")
    guild._roles[lord_role.id] = lord_role
    platform = DiscordChannelsPlatform(_StaticBot(guild))  # type: ignore[arg-type]
    return platform, guild, channel, lord_role


@pytest.mark.asyncio
async def test_apply_access_policy_single_edit(
    stack: tuple[DiscordChannelsPlatform, MockGuild, _CountingChannel, MockRole],
) -> None:
    platform, guild, channel, lord_role = stack
    policy = {"everyone_view": False, "view": ["Kingdoms Lord"]}

    await platform.apply_access_policy(str(guild.id), str(channel.id), policy)

    assert channel.edit_calls == 1  # ONE bulk write, never one PUT per target
    overwrites = channel.overwrites
    assert guild.me in overwrites  # the bot never locks itself out
    assert overwrites[guild.default_role].view_channel is False
    assert overwrites[guild.default_role].read_message_history is False
    assert overwrites[lord_role].view_channel is True
    assert overwrites[lord_role].read_message_history is True


@pytest.mark.asyncio
async def test_apply_access_policy_idempotent_no_second_write(
    stack: tuple[DiscordChannelsPlatform, MockGuild, _CountingChannel, MockRole],
) -> None:
    platform, guild, channel, _ = stack
    policy = {"everyone_view": False, "view": ["Kingdoms Lord"]}

    await platform.apply_access_policy(str(guild.id), str(channel.id), policy)
    await platform.apply_access_policy(str(guild.id), str(channel.id), policy)

    assert channel.edit_calls == 1  # converged — re-provisioning stays free


@pytest.mark.asyncio
async def test_apply_access_policy_preserves_foreign_overwrites(
    stack: tuple[DiscordChannelsPlatform, MockGuild, _CountingChannel, MockRole],
) -> None:
    platform, guild, channel, _ = stack
    foreign = MockRole(name="Officier du Corbeau")
    guild._roles[foreign.id] = foreign
    channel._overwrites[foreign] = discord.PermissionOverwrite(view_channel=True)

    await platform.apply_access_policy(str(guild.id), str(channel.id), {"everyone_view": False})

    assert channel.overwrites[foreign].view_channel is True  # untouched


@pytest.mark.asyncio
async def test_apply_access_policy_reapplies_on_drift(
    stack: tuple[DiscordChannelsPlatform, MockGuild, _CountingChannel, MockRole],
) -> None:
    platform, guild, channel, _ = stack
    await platform.apply_access_policy(str(guild.id), str(channel.id), {"everyone_view": False})
    channel._overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=True)  # manual drift

    await platform.apply_access_policy(str(guild.id), str(channel.id), {"everyone_view": False})

    assert channel.edit_calls == 2
    assert channel.overwrites[guild.default_role].view_channel is False
