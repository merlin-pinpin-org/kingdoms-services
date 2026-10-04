"""Unit tests for the /drasah medieval greeting command."""

from __future__ import annotations

import pytest

from kingdoms.discord.bot.factory import BotConfig, create_bot
from kingdoms.discord.drasah import GREETINGS, greeting_for


def test_greeting_for_fr_mentions_user() -> None:
    greeting = greeting_for("fr", "<@123>")
    assert "<@123>" in greeting


def test_greeting_for_unknown_locale_falls_back_to_english() -> None:
    assert greeting_for("de", "<@1>") in {t.format(user="<@1>") for t in GREETINGS["en"]}


def test_greeting_for_is_stable_per_user() -> None:
    assert greeting_for("en", "<@42>") == greeting_for("en", "<@42>")


@pytest.fixture
def config_dir() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[3] / "config")


def test_create_bot_registers_drasah_command(config_dir: str) -> None:
    bot = create_bot(BotConfig(config_dir=config_dir))
    assert "drasah" in {command.name for command in bot.tree.get_commands()}
