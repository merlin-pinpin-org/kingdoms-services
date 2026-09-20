"""Unit tests for the bot entry point (kingdoms-services#12, #34)."""

from __future__ import annotations

import logging

import pytest

from kingdoms.discord.bot import main as bot_main


def test_ready_log_line_is_stable() -> None:
    """CI smoke tests grep for this exact marker; it must not drift."""
    assert bot_main.READY_LOG_LINE == "KINGDOMS_BOT_READY"


def test_main_exits_nonzero_when_gateway_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A gateway startup failure must be a loud, non-zero exit."""

    async def failing_run_bot() -> None:
        raise RuntimeError("gateway unreachable")

    monkeypatch.setattr(bot_main, "run_bot", failing_run_bot)
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            bot_main.main()
    assert excinfo.value.code != 0


def test_main_swallows_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ctrl-C during the event loop is a clean shutdown, not a failure."""

    async def interrupted_run_bot() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(bot_main, "run_bot", interrupted_run_bot)
    bot_main.main()


def test_preflight_fails_without_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight is fail-closed on missing environment variables."""
    for var in ("MONGO_URI", "REDIS_URI", "DISCORD_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    assert bot_main.preflight() == 1
