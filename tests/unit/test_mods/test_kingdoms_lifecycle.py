"""The mod lifecycle hooks \u2014 unit tests on the register/setup_hook contract.

The regression this guards against: a rebuilt mod whose ``__init__``
carries no hooks loads silently (``register_mod`` logs "nothing to
wire") and the whole Discord surface disappears \u2014 no /kingdom, no
/kingdoms screens, dead persistent buttons (the drasah outage).
"""

from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from kingdoms.mods.kingdoms import register, setup_hook


class _FakeStatus:
    bot_admins: tuple[str, ...] = ("000000000",)


class _FakeConfig:
    mongo_uri: str = ""
    config_dir: Any = None


class _FakeBot:
    def __init__(self) -> None:
        self._client = discord.Client(intents=discord.Intents.none())
        self.tree: app_commands.CommandTree[discord.Client] = app_commands.CommandTree(self._client)
        self.status_service = _FakeStatus()
        self.roles_service = None
        self._dynamic_items: set[type] = set()

    def add_dynamic_items(self, *items: type) -> None:
        """Record the re-registered persistent classes (the client seam)."""
        self._dynamic_items.update(items)


def test_register_wires_the_full_command_surface() -> None:
    bot = _FakeBot()
    register(bot, _FakeConfig())
    names = {cmd.name for cmd in bot.tree.get_commands()}
    assert {"kingdom", "kingdoms", "kingdoms-admin"} <= names


def test_register_degrades_without_mongo() -> None:
    bot = _FakeBot()
    register(bot, _FakeConfig())
    assert bot.kingdoms_service is None


def test_setup_hook_registers_the_persistent_items() -> None:
    bot = _FakeBot()
    setup_hook(bot)
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomAdminButton,
        KingdomApplyButton,
        KingdomCandidatureButton,
        KingdomProfileButton,
        KingdomRequestButton,
    )

    expected = {
        KingdomApplyButton,
        KingdomCandidatureButton,
        KingdomProfileButton,
        KingdomRequestButton,
        KingdomAdminButton,
    }
    assert expected <= bot._dynamic_items


def test_setup_hook_never_raises_on_a_bare_bot() -> None:
    setup_hook(_FakeBot())
