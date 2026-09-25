"""SimCord journey for the bot logs channel (kingdoms-services#109, #52).

The startup announcement is now a lifecycle event of the per-guild bot
logs channel: this journey drives the **real bot** (``create_bot`` →
``on_ready`` → ``announce_startup`` → ``LogService`` → real discord.py
``create_text_channel``/``send`` through SimCord's fake HTTP) end to end,
with only the persistence seams swapped for in-memory fakes. It proves
the wiring, not the logic: unit tests cover the LogService itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.logs import (
    BOT_LOGS_CATEGORY,
    BOT_LOGS_CHANNEL_NAME,
    LogService,
    default_policy,
)
from kingdoms.core.services.state import StateService
from kingdoms.discord.logs_platform import DiscordLogsPlatform
from tests.mocks.state_mock import InMemoryStateStore

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class InMemoryLogsDatabase:
    """In-memory LogsDatabase (same contract as MongoLogsDatabase)."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}
        self.policies: dict[str, dict[str, Any]] = {}

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        return self.channels.pop(f"{guild_id}:{category}", None) is not None

    async def get_policy(self, guild_id: str, category: str) -> dict[str, Any] | None:
        return self.policies.get(f"{guild_id}:{category}")

    async def set_policy(self, guild_id: str, category: str, policy: dict[str, Any]) -> None:
        self.policies[f"{guild_id}:{category}"] = dict(policy)


class TestStartupAnnouncementJourney:
    """The startup announcement lands in each guild's bot logs channel."""

    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        """Attach a real LogService backed by in-memory persistence.

        ``create_bot`` leaves ``logs_service`` at None in tests (no
        ``mongo_uri``), so the journey wires the production stack — real
        ``DiscordLogsPlatform``, real ``LogService`` — with only the
        MongoDB and Redis seams replaced by in-memory fakes.
        """
        database = InMemoryLogsDatabase()
        self.logs_database = database
        kingdoms_bot.logs_service = LogService(
            database=database,  # type: ignore[arg-type]
            platform=DiscordLogsPlatform(kingdoms_bot),
            state=StateService(store=InMemoryStateStore()),  # type: ignore[arg-type]
        )
        return kingdoms_bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_startup_announcement_creates_and_fills_bot_logs_channel(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        guild = simcord_env.create_guild(name="Kingdom Test")

        # SimCord reaches READY with no guilds (guilds arrive via GUILD_CREATE
        # afterwards), so replay on_ready now that the guild is present.
        with simcord_env._bot_scope():
            simcord_env.bot.dispatch("ready")
        await simcord_env.settle()

        channels = guild.channels
        assert BOT_LOGS_CHANNEL_NAME in channels, f"channels: {list(channels)}"
        logs_channel = channels[BOT_LOGS_CHANNEL_NAME]
        history = logs_channel.history()
        assert history, "the startup announcement must be in the bot logs channel"
        message = history[0]
        assert message.embeds, "the announcement must carry the deploy-identity embed"
        embed = message.embeds[0]
        assert embed.title == "Kingdoms — Deployment"
        services = next(f.value for f in embed.fields if f.name == "Services")
        infra = next(f.value for f in embed.fields if f.name == "Infra")
        assert services and infra
        assert "-# kingdoms-deploy" in message.content

        stored = self.logs_database.channels[str(guild.id) + ":" + BOT_LOGS_CATEGORY]
        assert stored.name == BOT_LOGS_CHANNEL_NAME
        assert stored.channel_id == str(logs_channel.id)
        policy = await self.logs_database.get_policy(str(guild.id), BOT_LOGS_CATEGORY)
        expected = default_policy()
        assert policy is not None
        assert policy["default"] == expected["default"]
        assert policy["roles_with_view"] == expected["roles_with_view"]

        # The CI/CD-bot scenario: a fresh process with an empty database must
        # adopt the existing channel, never create a second one.
        fresh_database = InMemoryLogsDatabase()
        simcord_env.bot.logs_service = LogService(
            database=fresh_database,  # type: ignore[arg-type]
            platform=DiscordLogsPlatform(simcord_env.bot),  # type: ignore[arg-type]
            state=StateService(store=InMemoryStateStore()),  # type: ignore[arg-type]
        )
        with simcord_env._bot_scope():
            simcord_env.bot.dispatch("ready")
        await simcord_env.settle()

        bot_logs_channels = [name for name in guild.channels if name == BOT_LOGS_CHANNEL_NAME]
        assert len(bot_logs_channels) == 1, "a fresh database must adopt, not duplicate"
        assert (
            fresh_database.channels[str(guild.id) + ":" + BOT_LOGS_CATEGORY].channel_id
            == str(logs_channel.id)
        )
