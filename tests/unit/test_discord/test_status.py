"""Unit tests for the /status embed rendering (kingdoms-services#35)."""

from __future__ import annotations

import discord
import pytest

from kingdoms.core.services.mod_definition import (
    ChannelCategoryDef,
    ModDefinition,
    RoleDef,
)
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.status import StatusService
from kingdoms.discord.status import _human_uptime, build_status_embed


def make_status() -> StatusService:
    registry = ModRegistry(
        {
            "example": ModDefinition(
                name="example",
                channel_categories=(ChannelCategoryDef(key="announce", display_name="Annonces"),),
                roles=(RoleDef(key="member", display_name="Example Member"),),
            )
        }
    )
    return StatusService(registry=registry, bot_admins=type("A", (), {"user_ids": ("42",)})())


def test_human_uptime_renders_compact_durations() -> None:
    assert _human_uptime(0) == "0s"
    assert _human_uptime(59) == "59s"
    assert _human_uptime(61) == "1m 1s"
    assert _human_uptime(3661) == "1h 1m 1s"
    assert _human_uptime(90061) == "1d 1h 1m 1s"


def test_status_embed_contains_core_sections() -> None:
    embed = build_status_embed(make_status(), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert "Version" in fields
    assert "Uptime" in fields
    assert "42" in fields["Bot admins"]
    assert "*(none configured)*" in fields["Games"]
    assert "example" in fields["Enabled mods"]
    assert "`example:announce`" in fields["Enabled mods"]
    assert "`member`" in fields["Enabled mods"]


def test_status_embed_lists_mod_channels_and_roles() -> None:
    embed = build_status_embed(make_status(), guild=None)
    mods_field = next(f for f in embed.fields if f.name == "Enabled mods")
    assert "channels: `example:announce`" in mods_field.value
    assert "roles: `member`" in mods_field.value


def test_status_embed_flags_missing_bot_admins() -> None:
    registry = ModRegistry({})
    status = StatusService(registry=registry, bot_admins=type("A", (), {"user_ids": ()})())
    embed = build_status_embed(status, guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert "BOT_ADMINS" in fields["Bot admins"]


async def test_register_status_command_wires_a_status_command() -> None:
    from discord import app_commands

    from kingdoms.discord.status import register_status_command

    client = discord.Client(intents=discord.Intents.none())
    tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(client)
    register_status_command(tree, make_status())
    assert any(cmd.name == "status" for cmd in tree.get_commands())
    await client.close()


@pytest.mark.parametrize(
    ("total", "expect_days"),
    [(0, False), (90061, True), (86399, False)],
)
def test_human_uptime_renders_days_only_past_24h(total: int, expect_days: bool) -> None:
    assert ("d " in _human_uptime(total)) is expect_days
