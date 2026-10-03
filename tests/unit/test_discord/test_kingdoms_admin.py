"""The /kingdoms-admin command group — unit tests on the registered tree.

The registration contract: one guild-only ``kingdoms-admin`` group,
administrator-gated at Discord's click time, carrying the six T2 admin
subcommands, and no cross-instance leakage.
"""
from __future__ import annotations

import discord
from discord import app_commands

from kingdoms.discord.kingdoms_admin import DEFAULT_LOCALE, STRINGS, register_kingdoms_admin_command

EXPECTED_SUBCOMMANDS = (
    "launch",
    "reset",
    "season",
    "name",
    "assign",
    "replace",
)


def _build_tree() -> tuple[discord.Client, app_commands.CommandTree[discord.Client]]:
    client = discord.Client(intents=discord.Intents.none())
    tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(client)
    register_kingdoms_admin_command(tree, service=None, bot_admins=("1",))
    return client, tree


async def test_register_adds_the_admin_group() -> None:
    client, tree = _build_tree()
    group = next(cmd for cmd in tree.get_commands() if cmd.name == "kingdoms-admin")
    assert isinstance(group, app_commands.Group)
    assert group.guild_only is True
    assert group.default_permissions is not None
    assert group.default_permissions.administrator is True
    await client.close()


async def test_the_six_admin_subcommands_are_registered() -> None:
    client, tree = _build_tree()
    group = next(cmd for cmd in tree.get_commands() if cmd.name == "kingdoms-admin")
    assert isinstance(group, app_commands.Group)
    names = {cmd.name for cmd in group.commands}
    assert names == set(EXPECTED_SUBCOMMANDS)
    await client.close()


def test_strings_cover_both_locales() -> None:
    """Every admin key exists in both the FR and EN designer catalogs."""
    for locale in ("fr", "en"):
        catalog = STRINGS[locale]
        expected = (
            "group_description",
            "launch_name",
            "reset_name",
            "status_name",
            "name_name",
            "assign_name",
            "replace_name",
            "name_approve",
            "name_refuse",
            "enroll_role_king",
            "enroll_role_lord",
            "service_unavailable",
            "denied",
        )
        for key in expected:
            assert key in catalog, f"{locale} misses {key}"
    assert DEFAULT_LOCALE == "en"


def test_subcommand_names_are_discord_safe() -> None:
    """Discord names: lowercase, no space, within the 32-char limit."""
    for name in EXPECTED_SUBCOMMANDS:
        assert name == name.lower()
        assert " " not in name
        assert len(name) <= 32
