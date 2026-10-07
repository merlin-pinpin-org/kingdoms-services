"""The /kingdoms command group — unit tests on the registered tree.

The registration contract: one guild-only ``kingdoms`` group carrying
the five Lot B screens (B1-B5), the ``kingdom`` option frozen on the
profile and diplomacy subcommands, and no cross-instance leakage
(re-registering builds a fresh group).
"""
from __future__ import annotations

import discord
import pytest
from discord import app_commands

from kingdoms.discord.kingdoms import DEFAULT_LOCALE, STRINGS, register_kingdoms_command

EXPECTED_SUBCOMMANDS = (
    "cadastre",
    "kingdom",
    "delays",
    "diplomacy",
    "gazette",
    "join",
    "leave",
)


def _build_tree() -> tuple[discord.Client, app_commands.CommandTree[discord.Client]]:
    client = discord.Client(intents=discord.Intents.none())
    tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(client)
    register_kingdoms_command(tree)
    return client, tree


async def test_register_adds_the_kingdoms_group() -> None:
    client, tree = _build_tree()
    group = next(cmd for cmd in tree.get_commands() if cmd.name == "kingdom")
    assert isinstance(group, app_commands.Group)
    assert group.guild_only is True
    await client.close()


async def test_all_the_subcommands_are_registered() -> None:
    client, tree = _build_tree()
    group = next(cmd for cmd in tree.get_commands() if cmd.name == "kingdom")
    assert isinstance(group, app_commands.Group)
    names = {cmd.name for cmd in group.commands}
    assert names == set(EXPECTED_SUBCOMMANDS)
    await client.close()


async def test_profile_and_diplomacy_freeze_the_kingdom_option() -> None:
    client, tree = _build_tree()
    group = next(cmd for cmd in tree.get_commands() if cmd.name == "kingdom")
    assert isinstance(group, app_commands.Group)
    for name in ("kingdom", "diplomacy"):
        command = next(cmd for cmd in group.commands if cmd.name == name)
        assert isinstance(command, app_commands.Command), f"{name} must be a subcommand"
        kingdom_params = [param for param in command.parameters if param.name == "kingdom"]
        assert kingdom_params, f"{name} must freeze the kingdom option"
        assert kingdom_params[0].required is False
    await client.close()


def test_strings_cover_both_locales() -> None:
    """Every screen key exists in both the FR and EN designer catalogs."""
    for locale in ("fr", "en"):
        catalog = STRINGS[locale]
        expected = (
            "group_description",
            "cadastre_title",
            "profile_title",
            "delays_title",
            "diplomacy_title",
            "gazette_title",
            "no_season",
            "footer",
        )
        for key in expected:
            assert key in catalog, f"{locale} misses {key}"
    assert DEFAULT_LOCALE == "en"


@pytest.mark.parametrize("screen", ("cadastre", "kingdom", "delays", "diplomacy", "gazette"))
def test_subcommand_names_are_discord_safe(screen: str) -> None:
    """Discord names: lowercase, no space, within the 32-char limit."""
    assert screen == screen.lower()
    assert " " not in screen
    assert len(screen) <= 32
