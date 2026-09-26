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


def make_status(
    deploy_url: str = "",
    deploy_label: str = "",
    deploy_run_url: str = "",
    deploy_infra_label: str = "",
    deploy_infra_url: str = "",
    deploy_image: str = "",
    deploy_kind: str = "",
    deploy_ref: str = "",
    deploy_tree_url: str = "",
    deploy_ts: str = "",
    deploy_branch: str = "",
    deploy_pr_title: str = "",
    deploy_run_number: str = "",
    deploy_run_ts: str = "",
) -> StatusService:
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
        deploy_infra_label=deploy_infra_label,
        deploy_infra_url=deploy_infra_url,
        deploy_image=deploy_image,
        deploy_kind=deploy_kind,
        deploy_ref=deploy_ref,
        deploy_tree_url=deploy_tree_url,
        deploy_ts=deploy_ts,
        deploy_branch=deploy_branch,
        deploy_pr_title=deploy_pr_title,
        deploy_run_number=deploy_run_number,
        deploy_run_ts=deploy_run_ts,
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
    assert "Services" in fields
    assert "Uptime" in fields
    assert "Latency" in fields
    assert "Infra" in fields
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
    assert format_deploy(run_url, artifact_url) == f"Deployment [run]({run_url})"


def test_format_deploy_shows_infra_state_and_run_when_both_provided() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    infra_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/tree/9691aca"
    result = format_deploy(run_url, "", "deploy/test@9691aca", infra_url)
    infra_repo = "https://github.com/merlin-pinpin-org/kingdoms-infra"
    assert result == (
        f"Branch [deploy/test]({infra_repo}/tree/deploy/test)\n"
        f"Commit [9691aca]({infra_repo}/commit/9691aca)\n"
        f"Files [9691aca]({infra_url})\n"
        f"Deployment [run]({run_url})"
    )


def test_format_deploy_skips_infra_label_without_url() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    assert format_deploy(run_url, "", "deploy/test@9691aca", "") == f"Deployment [run]({run_url})"


def test_format_deploy_falls_back_to_the_artifact_link() -> None:
    artifact_url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    assert format_deploy("", artifact_url) == f"[deploy]({artifact_url})"


def test_format_deploy_marks_unknown_values_na() -> None:
    assert format_deploy("", "") == "n/a"


def test_status_embed_deploy_field_reads_status_service() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    infra_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/tree/9691aca"
    status = make_status(deploy_run_url=run_url, deploy_infra_label="deploy/test@9691aca", deploy_infra_url=infra_url)
    embed = build_status_embed(status, guild=None)
    fields = {f.name: f.value for f in embed.fields}
    infra_repo = "https://github.com/merlin-pinpin-org/kingdoms-infra"
    assert fields["Infra"] == (
        f"Branch [deploy/test]({infra_repo}/tree/deploy/test)\n"
        f"Commit [9691aca]({infra_repo}/commit/9691aca)\n"
        f"Files [9691aca]({infra_url})\n"
        f"Deployment [run]({run_url})"
    )


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
    assert fields["Services"] == "[pr-12-20260923-abcdef0](" + url + ")"


def test_status_embed_deploy_defaults_to_na() -> None:
    embed = build_status_embed(make_status(), guild=None)
    fields = {f.name: f.value for f in embed.fields}
    assert fields["Infra"] == "n/a"


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


class _FakeCommand:
    """Minimal slash command stand-in (name + description)."""

    def __init__(self, name: str, description: str = "fake") -> None:
        self.name = name
        self.description = description


class _FakeParent:
    """Group/cog stand-in owning commands (duck-typed root_parent)."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeGroupedCommand(_FakeCommand):
    """Slash command bound to a group (the tree equivalent of a cog)."""

    def __init__(self, name: str, parent: _FakeParent) -> None:
        super().__init__(name)
        self.root_parent = parent


class _FakeContextMenu:
    """Context menu stand-in: not a slash command, must be skipped."""

    name = "Message context action"


def test_format_commands_groups_by_owner_and_lists_core_last() -> None:
    from kingdoms.discord.status import format_commands

    register = _FakeGroupedCommand("register", _FakeParent("example"))
    whois = _FakeGroupedCommand("whois", _FakeParent("example"))
    result = format_commands([register, whois, _FakeCommand("status")])
    lines = result.split("\n")
    assert lines[0] == "**example**: /register, /whois"
    assert lines[1] == "**core**: /status"


def test_format_commands_renders_empty_tree() -> None:
    from kingdoms.discord.status import format_commands

    assert format_commands([]) == "*(none)*"


def test_format_commands_skips_context_menus() -> None:
    from kingdoms.discord.status import format_commands

    result = format_commands([_FakeCommand("status"), _FakeContextMenu()])
    assert result == "**core**: /status"


def test_format_version_pr_includes_the_title_when_known() -> None:
    url = "https://github.com/merlin-pinpin-org/kingdoms-services/pull/78#issuecomment-1"
    result = format_version("pr-78-...", url, kind="pr", ref="78", pr_title="Add the ping command")
    expected = (
        "Pull-request [#78]"
        "(https://github.com/merlin-pinpin-org/kingdoms-services/pull/78#issuecomment-1) “Add the ping command”"
    )
    assert result == expected


def test_format_version_pr_links_the_deployment_comment() -> None:
    url = "https://github.com/merlin-pinpin-org/kingdoms-services/pull/78#issuecomment-1"
    assert format_version("pr-78-...", url, kind="pr", ref="78") == f"Pull-request [#78]({url})"


def test_format_version_main_links_commit_with_relative_time() -> None:
    commit_url = "https://github.com/merlin-pinpin-org/kingdoms-services/commit/abcdef0"
    result = format_version("main@abcdef0", commit_url, kind="main", ref="abcdef0", ts="1727100000")
    assert result == f"[Commit abcdef0]({commit_url}) <t:1727100000:R>"


def test_format_version_release_links_the_release() -> None:
    release_url = "https://github.com/merlin-pinpin-org/kingdoms-services/releases/tag/v0.1.0"
    result = format_version("v0.1.0", release_url, kind="release", ref="v0.1.0")
    assert result == f"Release [v0.1.0]({release_url})"


def test_format_deploy_renders_deployment_number_with_relative_time() -> None:
    run_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/123"
    infra_url = "https://github.com/merlin-pinpin-org/kingdoms-infra/tree/9691aca"
    result = format_deploy(
        run_url, "", "deploy/test@9691aca", infra_url, "456", "1727100000", infra_commit_ts="1727000000"
    )
    infra_repo = "https://github.com/merlin-pinpin-org/kingdoms-infra"
    assert result == (
        f"Branch [deploy/test]({infra_repo}/tree/deploy/test)\n"
        f"Commit [9691aca]({infra_repo}/commit/9691aca) <t:1727000000:R>\n"
        f"Files [9691aca]({infra_url})\n"
        f"Deployment [#456]({run_url}) <t:1727100000:R>"
    )


def test_guild_admins_exclude_bots() -> None:
    from kingdoms.discord.status import _guild_admin_ids

    guild = MockGuild(name="Test Guild")
    human_admin = _FakeGuildAdmin(1)
    bot_member = MockMember(id=2, name="KingdomsBot", bot=True, roles=[MockRole(permissions=["manage_guild"])])
    guild._members[1] = human_admin
    guild._members[2] = bot_member
    assert _guild_admin_ids(guild, bot_user_id=2) == [1]


def test_format_services_section_appends_the_pinned_image() -> None:
    from kingdoms.discord.status import format_services_section

    image = "ghcr.io/merlin-pinpin-org/kingdoms-services:pr-12-20260923-abcdef0"
    package = "https://github.com/merlin-pinpin-org/kingdoms-services/pkgs/container/kingdoms-services"
    version_line = "[Pull-request #12](https://github.com/merlin-pinpin-org/kingdoms-services/pull/12#issuecomment-1)"
    result = format_services_section(version_line, image, kind="pr")
    assert result == (version_line + f"\nImage [20260923-abcdef0]({package})"), "the tag renders shortened"


def test_format_services_section_pr_renders_the_commit_line() -> None:
    from kingdoms.discord.status import format_services_section

    sha = "a10cdc793c7cb3be43d51464078225250f3ffabd"
    tree_url = f"https://github.com/merlin-pinpin-org/kingdoms-services/tree/{sha}"
    version_line = '[Pull-request #12 "Add the ping command"](https://github.com/merlin-pinpin-org/kingdoms-services/pull/12#issuecomment-1)'
    result = format_services_section(
        version_line,
        "",
        kind="pr",
        branch="vibe/ping-19c915",
        tree_url=tree_url,
        ts="1727100000",
        commit_ts="1727000000",
    )
    repo = "https://github.com/merlin-pinpin-org/kingdoms-services"
    assert "Commit [a10cdc7]" in result
    assert f"[a10cdc7]({repo}/commit/a10cdc7)" in result, "the commit links the sha7 (announcement parity)"
    assert f"Files [a10cdc7]({tree_url})" in result
    assert "([tree](" not in result
    assert "<t:1727000000:R>" in result, "the commit line carries the committed date"
    assert "<t:1727100000:R>" not in result, "the build date never renders on the commit line"
    assert result.index("Branch [vibe/ping-19c915]") < result.index("Commit [a10cdc7]")
    assert result.index("Commit [a10cdc7]") < result.index("Pull-request #12")


def test_format_services_section_main_keeps_no_duplicate_commit_line() -> None:
    from kingdoms.discord.status import format_services_section

    # kind=main: the version line already IS the commit line.
    tree_url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    version_line = "[Commit abcdef0](x) [tree](y) <t:1:R>"
    result = format_services_section(version_line, "", kind="main", tree_url=tree_url)
    assert result.count("Commit ") == 1


def test_format_services_section_without_image_keeps_the_version_line() -> None:
    from kingdoms.discord.status import format_services_section

    result = format_services_section("v0.1.0", "", kind="release")
    assert result == "v0.1.0"


def test_format_services_section_untyped_fallback_keeps_deploy_link() -> None:
    from kingdoms.discord.status import format_services_section

    url = "https://github.com/merlin-pinpin-org/kingdoms-services/tree/abcdef0"
    result = format_services_section("[main@abcdef0](x)", "", kind="", deploy_url=url)
    assert result == f"[main@abcdef0](x)\n[deploy]({url})"
