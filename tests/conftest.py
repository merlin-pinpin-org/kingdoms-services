"""Shared pytest fixtures.

Behavioral tests (journeys through the real discord.py dispatch) run on
SimCord: the bot under test runs unmodified against an in-memory virtual
Discord — no network, no token (see kingdoms/docs/architecture/testing.md).
The ``simcord_env`` fixture is provided by the SimCord pytest plugin; each
journey test class overrides ``simcord_bot`` with the bot it drives.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio as the async backend."""
    return "asyncio"
