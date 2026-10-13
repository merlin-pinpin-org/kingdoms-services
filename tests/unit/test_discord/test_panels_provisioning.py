"""Unit tests for the Panels SDK provisioning — the tree rules + adoption.

These pin the channel-tree contract of docs/architecture/channel-tree.md
(the kingdoms repo): the scope folders provisioned in the guild's
locale, the canonical order, the read-only menu channels, the admin
visibility — and the legacy adoption (rename + move in place, ids
unchanged, so the managed services and their pins survive).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.ui.panels_provisioning import (
    _Platform,
    _sorted_channels,
    provision_scope,
)
from kingdoms.discord.ui.panels_scopes import (
    AOE2_SCOPE,
    BOT_SCOPE,
    ROOT_SCOPE,
    register_platform_scopes,
)
from tests.mocks.discord_mock import (
    MockGuild,
    MockTextChannel,
)

CONFIG_DIR = str(Path(__file__).resolve().parents[3] / "config")


def _translate(key: str, locale: str) -> str:
    """Render through the real catalog (the FR naming is the contract)."""
    catalog = MessageCatalog(CONFIG_DIR)
    rendered = catalog.render(key, locale)
    return rendered if rendered != key else catalog.render(key, "en")


class TestCanonicalOrder:
    def test_the_canonical_order_sorts_the_bands(self) -> None:
        decls = _sorted_channels(AOE2_SCOPE)
        keys = [d.key for d in decls]
        assert keys[0] == "general"
        assert keys[1] == "discussions"
        assert keys[-2] == "admin"
        assert keys[-1] == "decisions"
        assert keys.index("civs") < keys.index("admin")

    def test_the_bot_scope_orders_logs_first(self) -> None:
        assert [d.key for d in _sorted_channels(BOT_SCOPE)] == ["logs", "admin", "decisions"]


class TestRootScope:
    @pytest.mark.asyncio
    async def test_the_root_scope_creates_uncategorised_channels(self) -> None:
        guild = MockGuild()
        resolved = await provision_scope(guild, ROOT_SCOPE, "fr", _translate)
        assert set(resolved) == {"general", "discussions"}
        general = next(c for c in guild.text_channels if c.name == "général")
        assert general.category is None
        assert len(guild.categories) == 0


class TestProvisioning:
    @pytest.mark.asyncio
    async def test_a_scope_provisions_category_and_channels(self) -> None:
        guild = MockGuild()
        resolved = await provision_scope(guild, BOT_SCOPE, "fr", _translate)
        assert set(resolved) == {"bot", "logs", "admin", "decisions"}
        names = {c.name for c in guild.text_channels}
        assert "logs" in names
        assert "décisions" in names  # translated at creation (FR guild)
        logs = next(c for c in guild.text_channels if c.name == "logs")
        assert logs.category is not None
        assert logs.category.name == "bot"

    @pytest.mark.asyncio
    async def test_provisioning_is_idempotent(self) -> None:
        guild = MockGuild()
        first = await provision_scope(guild, BOT_SCOPE, "fr", _translate)
        second = await provision_scope(guild, BOT_SCOPE, "fr", _translate)
        assert first == second
        assert len(guild.channels) == 4  # category + 3 channels, no duplicates

    @pytest.mark.asyncio
    async def test_the_games_scope_creates_the_forums(self) -> None:
        guild = MockGuild()
        resolved = await provision_scope(guild, AOE2_SCOPE, "en", _translate)
        assert resolved["civs"] and resolved["maps"] and resolved["map-pools"]
        forums = {f.name: f for f in guild.forums}
        assert set(forums) == {"civs", "maps", "map pools"}  # EN locale: translated names
        for forum in forums.values():
            assert forum.category is not None
            assert forum.category.name == "games/aoe2"



class TestPlatformScopes:
    def test_registration_is_idempotent(self) -> None:
        register_platform_scopes()
        register_platform_scopes()
        from kingdoms.discord.ui.panels import registered_scopes

        keys = [spec.scope for spec in registered_scopes()]
        assert keys.count("bot") == 1
        assert keys.count("games/aoe2") == 1

    def test_every_decl_carries_a_label_key(self) -> None:
        for spec in (BOT_SCOPE, AOE2_SCOPE):
            for decl in spec.channels:
                assert decl.label_key.startswith("ui.panels.")



class TestAdoptionSeams:
    @pytest.mark.asyncio
    async def test_ensure_channel_moves_a_stray_channel_into_the_folder(self) -> None:
        guild = MockGuild()
        platform: Any = _Platform(guild)
        stray = MockTextChannel(name="discussions", guild=guild)
        guild._channels[stray.id] = stray
        category_id = await platform.ensure_category("games")
        channel_id = await platform.ensure_channel("discussions", category_id)
        assert channel_id == str(stray.id)
        assert stray.category is not None
        assert stray.category.name == "games"
