"""Shared pytest fixtures.

Behavioral tests (journeys through the real discord.py dispatch) run on
SimCord: the bot under test runs unmodified against an in-memory virtual
Discord — no network, no token (see kingdoms/docs/architecture/testing.md).

The ``simcord_env`` fixture is provided by the SimCord pytest plugin. Since
kingdoms-services#12 the shared ``simcord_bot`` fixture builds the real bot
through the production factory (``create_bot``), so journeys exercise the
actual dispatch, command tree and service wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.discord.bot.factory import BotConfig, KingdomsBot, create_bot

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio as the async backend."""
    return "asyncio"


@pytest.fixture
def bot_config() -> BotConfig:
    """A test BotConfig pointing at the repository's real config directory."""
    return BotConfig(config_dir=REPO_ROOT / "config")


@pytest.fixture
def kingdoms_bot(bot_config: BotConfig) -> KingdomsBot:
    """The real bot, built by the production factory."""
    return create_bot(bot_config)


@pytest.fixture
def simcord_bot(kingdoms_bot: KingdomsBot) -> KingdomsBot:
    """Hand the real bot to SimCord's environment runner."""
    return kingdoms_bot
