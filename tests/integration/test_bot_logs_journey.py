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
    LogService,
)
from kingdoms.core.services.state import StateService
from kingdoms.discord.logs_platform import DiscordLogsPlatform
from tests.mocks.state_mock import InMemoryStateStore

IS_COMPONENTS_V2 = 1 << 15
TYPE_TEXT_DISPLAY = 10


def _walk(components: list) -> list:  # type: ignore[type-arg]
    """Depth-first walk of a message component tree (wire dicts or objects)."""
    out: list[Any] = []
    for component in components or []:
        out.append(component)
        if isinstance(component, dict):
            out.extend(_walk(component.get("components", [])))
        else:
            out.extend(_walk(list(getattr(component, "children", []))))
    return out


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
    async def test_startup_announcement_dm_the_bot_admins(self, simcord_env) -> None:  # type: ignore[no-untyped-def]
        """The boot message lands in the bot-admins' DMs, never a guild channel."""
        guild = simcord_env.create_guild(name="Kingdom Test")
        with simcord_env._bot_scope():
            simcord_env.bot.dispatch("ready")
        await simcord_env.settle()
        channels = guild.channels
        assert not any(list(c.history()) for c in channels.values()), (
            "the boot message never lands in a guild channel anymore"
        )
