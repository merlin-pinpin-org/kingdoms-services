"""Tests for the managed-channel service: the shared provisioning lifecycle.

Every managed surface (the admin home, the guild home) resolves its
channel the same way — cache-aside: Redis → Mongo → adoption → creation
— with the surface's visibility policy re-applied at every resolution
and a stale persisted channel cleaned up.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.managed_channel import ManagedChannelService


class _FakePlatform:
    """Platform stand-in recording every provisioning call."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.policies: list[tuple[str, str]] = []
        self.existing: set[str] = {"555"}
        self.by_name: dict[str, str] = {}

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        return self.by_name.get(name)

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        self.created.append(name)
        self.existing.add("999")
        return "999"

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        return channel_id in self.existing

    async def apply_policy(self, guild_id: str, channel_id: str) -> None:
        self.policies.append((guild_id, channel_id))

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        return "9001"


class _FakeDatabase:
    """Database stand-in: an in-memory channel registry."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}
        self.deleted: list[str] = []

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        key = f"{guild_id}:{category}"
        if key in self.channels:
            del self.channels[key]
            self.deleted.append(key)
            return True
        return False


class _FakeState:
    """State cache stand-in."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], dict[str, Any]] = {}

    async def get_state(self, scope: str, key: str) -> dict[str, Any] | None:
        return self.store.get((scope, key))

    async def set_state(self, scope: str, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        self.store[(scope, key)] = value
        return True


def _service(platform: _FakePlatform, db: _FakeDatabase, state: _FakeState) -> ManagedChannelService:
    return ManagedChannelService(platform, db, category="home", name="home-channel", state=state)


@pytest.mark.asyncio
async def test_resolve_creates_when_absent() -> None:
    """A guild without any trace gets the channel created and persisted."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    channel_id = await _service(platform, db, state).resolve_channel("42")
    assert channel_id == "999"
    assert platform.created == ["home-channel"]
    assert "42:home" in db.channels
    assert platform.policies == [("42", "999")]


@pytest.mark.asyncio
async def test_resolve_adopts_an_existing_channel_by_name() -> None:
    """A channel with the right name is adopted, not recreated."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    platform.by_name = {"home-channel": "777"}
    channel_id = await _service(platform, db, state).resolve_channel("42")
    assert channel_id == "777"
    assert platform.created == []
    assert db.channels["42:home"].channel_id == "777"


@pytest.mark.asyncio
async def test_resolve_serves_the_cache_when_the_channel_exists() -> None:
    """A warm cache entry pointing at a live channel short-circuits Mongo."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    await state.set_state("channels", "managed_channel:home:42", {"channel_id": "555"})
    channel_id = await _service(platform, db, state).resolve_channel("42")
    assert channel_id == "555"
    assert db.channels == {}
    assert platform.policies == [("42", "555")]


@pytest.mark.asyncio
async def test_resolve_drops_a_stale_persisted_channel() -> None:
    """A persisted channel gone from the platform is deleted, then reprovisioned."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    db.channels["42:home"] = ChannelModel(
        _id="42:home",
        guild_id="42",
        platform="discord",
        category="home",
        channel_id="555",
        name="home-channel",
    )
    platform.existing = set()
    channel_id = await _service(platform, db, state).resolve_channel("42")
    assert channel_id == "999"
    assert db.deleted == ["42:home"]


@pytest.mark.asyncio
async def test_set_channel_persists_and_policies() -> None:
    """Routing to an existing channel persists it and applies the policy."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    await _service(platform, db, state).set_channel("42", "555")
    assert db.channels["42:home"].channel_id == "555"
    assert platform.policies == [("42", "555")]


@pytest.mark.asyncio
async def test_set_channel_rejects_an_unknown_channel() -> None:
    """Routing to a channel the platform cannot see is an error."""
    platform, db, state = _FakePlatform(), _FakeDatabase(), _FakeState()
    platform.existing = set()
    with pytest.raises(ValueError):
        await _service(platform, db, state).set_channel("42", "555")
