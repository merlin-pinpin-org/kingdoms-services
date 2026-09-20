"""Unit tests for the bot factory (kingdoms-services#12)."""

from __future__ import annotations

import logging

import discord
import pytest

from kingdoms.discord.bot.factory import (
    READY_LOG_LINE,
    BotConfig,
    KingdomsBot,
    create_bot,
)


@pytest.fixture
def config_dir() -> str:
    """Path to the repository's real config directory."""
    from pathlib import Path

    return str(Path(__file__).resolve().parents[3] / "config")


def test_bot_config_from_env_reads_environment() -> None:
    config = BotConfig.from_env(
        {
            "MONGO_URI": "mongodb://localhost:27017",
            "REDIS_URI": "redis://localhost:6379",
            "DISCORD_TOKEN": "token",
            "BOT_ADMINS": "111,222",
            "CICD_GUILD_ID": "12345",
            "LOG_LEVEL": "DEBUG",
        }
    )
    assert config.mongo_uri == "mongodb://localhost:27017"
    assert config.redis_uri == "redis://localhost:6379"
    assert config.discord_token == "token"  # noqa: S105 — test value, not a credential
    assert config.bot_admins == "111,222"
    assert config.sync_guild_id == "12345"
    assert config.log_level == "DEBUG"


def test_bot_config_from_env_defaults_are_empty() -> None:
    config = BotConfig.from_env({})
    assert config.mongo_uri == ""
    assert config.sync_guild_id == ""


def test_create_bot_builds_client_with_tree_and_services(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    assert isinstance(bot, KingdomsBot)
    assert isinstance(bot, discord.Client)
    assert isinstance(bot.tree, discord.app_commands.CommandTree)
    assert bot.status_service is not None
    assert "status" in {command.name for command in bot.tree.get_commands()}


def test_create_bot_keeps_ready_marker_contract(config_dir: str) -> None:
    """CI smoke greps KINGDOMS_BOT_READY; the factory must log the same marker."""
    assert READY_LOG_LINE == "KINGDOMS_BOT_READY"


async def test_ready_marker_logged_on_ready(config_dir: str, caplog: pytest.LogCaptureFixture) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    with caplog.at_level(logging.INFO, logger="kingdoms.bot"):
        await bot.on_ready()
    assert any(READY_LOG_LINE in record.message for record in caplog.records)


def test_create_bot_parses_bot_admins(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir, bot_admins="111,222"))
    assert bot.status_service.bot_admins == ("111", "222")
