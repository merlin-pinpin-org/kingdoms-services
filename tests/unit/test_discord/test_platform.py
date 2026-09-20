"""Unit tests for the Discord platform skeletons."""

from __future__ import annotations

import pytest

from kingdoms.discord.platform.adapters import to_core_channel, to_core_message, to_core_user
from kingdoms.discord.platform.discord_platform import DiscordPlatform


async def test_discord_platform_raises_until_implemented() -> None:
    platform = DiscordPlatform()
    with pytest.raises(NotImplementedError):
        await platform.send_message(None, "hello")  # type: ignore[arg-type]


def test_adapters_raise_until_implemented() -> None:
    with pytest.raises(NotImplementedError):
        to_core_user(None)
    with pytest.raises(NotImplementedError):
        to_core_channel(None)
    with pytest.raises(NotImplementedError):
        to_core_message(None)
