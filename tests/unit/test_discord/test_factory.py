"""Unit tests for the bot factory (kingdoms-services#12)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

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


class _FakeLogsService:
    """Minimal LogService stand-in recording resolve_channel calls."""

    def __init__(self) -> None:
        self.resolved: list[str] = []

    async def get_locale(self, guild_id: str) -> str:
        return "en"

    async def resolve_channel(self, guild_id: str) -> str:
        self.resolved.append(guild_id)
        return "logs-channel"


class _FakeAdminChannelService:
    """Minimal AdminChannelService stand-in recording resolve_channel calls."""

    def __init__(self) -> None:
        self.resolved: list[tuple[str, tuple[str, ...]]] = []

    async def resolve_channel(self, guild_id: str, admin_ids: tuple[str, ...] = ()) -> str:
        self.resolved.append((guild_id, admin_ids))
        return "admin-channel"


@pytest.mark.asyncio
async def test_provision_default_channels_resolves_both_services(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    logs = _FakeLogsService()
    admin = _FakeAdminChannelService()
    bot.logs_service = logs  # type: ignore[assignment]
    bot.admin_channel_service = admin  # type: ignore[assignment]
    bot._connection._guilds[42] = SimpleNamespace(id=42)
    await bot._provision_default_channels()
    assert logs.resolved == ["42"]
    assert admin.resolved == [("42", bot.status_service.bot_admins)]


@pytest.mark.asyncio
async def test_provision_default_channels_gated_by_announce_enabled(config_dir: str) -> None:
    """The CI/CD smoke bot (KINGDOMS_ANNOUNCE_ENABLED=0) never provisions channels."""
    bot = create_bot(BotConfig(config_dir=config_dir, announce_enabled="0"))
    logs = _FakeLogsService()
    bot.logs_service = logs  # type: ignore[assignment]
    bot._connection._guilds[42] = SimpleNamespace(id=42)
    await bot._provision_default_channels()
    assert logs.resolved == []


@pytest.mark.asyncio
async def test_provision_default_channels_runs_once_across_reconnects(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    logs = _FakeLogsService()
    bot.logs_service = logs  # type: ignore[assignment]
    bot._connection._guilds[42] = SimpleNamespace(id=42)
    await bot._provision_default_channels()
    await bot._provision_default_channels()
    assert logs.resolved == ["42"]


@pytest.mark.asyncio
async def test_provision_default_channels_survives_one_guild_failure(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    logs = _FakeLogsService()
    admin = _FakeAdminChannelService()
    bot.logs_service = logs  # type: ignore[assignment]
    bot.admin_channel_service = admin  # type: ignore[assignment]
    bot._connection._guilds[42] = SimpleNamespace(id=42)

    async def _boom(guild_id: str) -> str:
        raise RuntimeError("logs channel resolution exploded")

    bot.logs_service.resolve_channel = _boom  # type: ignore[method-assign]
    await bot._provision_default_channels()
    assert admin.resolved == [("42", bot.status_service.bot_admins)]
