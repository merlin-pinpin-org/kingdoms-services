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

import socket
from pathlib import Path

import pytest

from kingdoms.discord.bot.factory import BotConfig, KingdomsBot, create_bot

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def network_free():  # type: ignore[no-untyped-def]
    """Fail any test that attempts a real network connection (hermeticity).

    Tests are in-memory and network-free by design (kingdoms-services#106):
    SimCord replaces the discord.py HTTP client and gateway, so a real
    socket connect means a journey (or the code under test wired for a
    live service) escaped its double — the run must fail, not silently
    reach the network.
    """
    original_connect = socket.socket.connect

    def blocked_connect(self: socket.socket, address: object) -> None:  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        loopback = {"127.0.0.1", "::1", "localhost", "", None}
        if isinstance(host, bytes):
            host = host.decode("utf-8", errors="replace")
        if host in loopback:
            original_connect(self, address)  # type: ignore[no-untyped-def]
            return
        pytest.fail(
            f"A test attempted a real network connection to {address!r}: "
            "tests must stay in-memory and network-free (kingdoms-services#106) "
            "— SimCord replaces the discord.py HTTP client and gateway; "
            "loopback only is tolerated for the local /healthz server."
        )

    socket.socket.connect = blocked_connect  # type: ignore[method-assign]
    yield
    socket.socket.connect = original_connect  # type: ignore[method-assign]


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
