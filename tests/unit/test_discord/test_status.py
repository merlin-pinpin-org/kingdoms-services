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
from kingdoms.core.services.status import StatusService, format_version
from kingdoms.discord.status import (
    _human_uptime,
    build_status_embed,
    format_admins,
    format_deploy,
    format_latency,
)
from tests.mocks.discord_mock import MockGuild, MockMember, MockRole


class _FakeGuildAdmin(MockMember):
    """Guild admin stand-in: discord.py derives guild_permissions from roles."""

    def __init__(self, user_id: int) -> None:
        super().__init__(id=user_id, name="Admin", roles=[MockRole(permissions=["administrator"])])
        self.timed_out_until = None


def make_status(deploy_url: str = "", deploy_label: str = "", deploy_run_url: str = "") -> StatusService:
    registry = ModRegistry(
        {
            "example": ModDefinition(
                name="example",
                channel_categories=(ChannelCategoryDef(key="announce", display_name="Annonces"),),
                roles=(RoleDef(key="member", display_name="Example Member"),),
            )
        }
    )
    return StatusService(
        registry=registry,
        bot_admins=type("A", (), {"user_ids": ("42",)})(),
        deploy_url=deploy_url,
        deploy_label=deploy_label,
        deploy_run_url=deploy_run_url,
    )


def make_guild(admin_id: int = 1) -> MockGuild:
    guild = MockGuild(name="Test Guild")
    guild._members[admin_id] = _FakeGuildAdmin(admin_id)
    guild.add_member(MockMember(name="Plain"))
    return guild


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
    assert "Latency" in fields
    assert "Deploy" in fields
    assert "- <@42>" in fields["Admins"]
    assert "*(none configured)*" in fields["Games"]
    assert "example" in fields["Enabled mods"]
    assert "`example:announce`" in fields["Enabled mods"]
    assert "`member`" in fields["Enabled mods"]


def test_status_embed_admins_section_mentions_bot_and_guild_admins() -> None:
    embed = build_status_embed(make_status(), guild=make_guild(admin_id=77))
    admins = next(f for f in embed.fields if f.name == "Admins")
    assert "- <@42>" in admins.value
    assert "- <@77>" in admins.value
    assert "Test Guild" not in admins.value


def test_format_admins_without_guild_lists_bot_admins_only() -> None:
    assert format_admins(("42",), None) == "- <@42>"


def test_format_admins_flags_missing_bot_admins() -> None:
    assert "BOT_ADMINS" in format_admins((), None)


def test_format_deploy_prefers_the_deploy_run_link() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    artifact_url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    assert format_deploy(run_url, artifact_url) == f"[deploy run]({run_url})"


def test_format_deploy_falls_back_to_the_artifact_link() -> None:
    artifact_url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    assert format_deploy("", artifact_url) == f"[deploy]({artifact_url})"


def test_format_deploy_marks_unknown_values_na() -> None:
    assert format_deploy("", "") == "n/a"


def test_status_embed_deploy_field_reads_status_service() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    embed = build_status_embed(make_status(deploy_run_url=run_url), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Deploy"] == f"[deploy run]({run_url})"


def test_format_version_renders_labeled_link() -> None:
    url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    assert format_version("pr-12-20260923-abcdef0", url) == f"[pr-12-20260923-abcdef0]({url})"


def test_format_version_bare_label_without_url() -> None:
    assert format_version("v0.1.0", "") == "v0.1.0"


def test_format_version_falls_back_to_package_version() -> None:
    from kingdoms import __version__

    assert format_version("", "") == __version__


def test_status_embed_version_field_is_labeled_link() -> None:
    url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    embed = build_status_embed(make_status(deploy_url=url, deploy_label="pr-12-20260923-abcdef0"), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Version"] == "[pr-12-20260923-abcdef0](" + url + ")"


def test_status_embed_deploy_defaults_to_na() -> None:
    embed = build_status_embed(make_status(), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Deploy"] == "n/a"


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
    assert "BOT_ADMINS" in fields["Admins"]


def test_format_latency_renders_integer_milliseconds() -> None:
    assert format_latency(0.1234) == "123 ms"
    assert format_latency(0.0005) == "1 ms" if round(0.0005 * 1000) == 1 else format_latency(0.0005) == "0 ms"


def test_format_latency_marks_unknown_values_na() -> None:
    assert format_latency(None) == "n/a"
    assert format_latency(-1.0) == "n/a"


def test_status_embed_contains_latency_field() -> None:
    embed = build_status_embed(make_status(), guild=None, latency=0.25)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Latency"] == "250 ms"


def test_status_embed_latency_defaults_to_na() -> None:
    embed = build_status_embed(make_status(), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Latency"] == "n/a"


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
