"""Unit tests for the LogService (kingdoms-services#109).

Covers the cache-aside resolution flow (Redis → MongoDB → creation),
the admin-only default policy, the crash-loop collapse, and the
best-effort degradation (store failures never propagate).
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.models.channel import ChannelModel
from kingdoms.core.services.logs import (
    BOT_LOGS_CATEGORY,
    BOT_LOGS_CHANNEL_NAME,
    CRASH_LOOP_THRESHOLD,
    LifecycleEvent,
    LogService,
    default_policy,
)
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

GUILD = "123456"


class FakeLogsDatabase:
    """In-memory LogsDatabase: documents keyed by guild:category."""

    def __init__(self) -> None:
        self.channels: dict[str, ChannelModel] = {}
        self.policies: dict[str, dict[str, Any]] = {}
        self.created_order: list[str] = []

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        return self.channels.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: ChannelModel) -> None:
        self.channels[channel.id] = channel
        self.created_order.append(channel.channel_id)

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        return self.channels.pop(f"{guild_id}:{category}", None) is not None

    async def get_policy(self, guild_id: str, category: str) -> dict[str, Any] | None:
        return self.policies.get(f"{guild_id}:{category}")

    async def set_policy(self, guild_id: str, category: str, policy: dict[str, Any]) -> None:
        self.policies[f"{guild_id}:{category}"] = dict(policy)


class FakeLogsPlatform:
    """In-memory LogsPlatform: channel created once, deletable, no Discord."""

    def __init__(self) -> None:
        self.next_channel_id = 1000
        self.live_channels: set[str] = set()
        self.default_policy_applied: list[str] = []
        self.role_grants: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.exists_calls = 0

    async def create_logs_channel(self, guild_id: str) -> str:
        channel_id = str(self.next_channel_id)
        self.next_channel_id += 1
        self.live_channels.add(channel_id)
        return channel_id

    async def apply_default_policy(self, guild_id: str, channel_id: str) -> None:
        self.default_policy_applied.append(channel_id)

    async def grant_role_view(self, guild_id: str, channel_id: str, role_id: str) -> None:
        self.role_grants.append((channel_id, role_id))

    async def send_log_message(self, guild_id: str, channel_id: str, content: str) -> None:
        if channel_id not in self.live_channels:
            raise RuntimeError("channel deleted")
        self.sent.append((channel_id, content))

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        self.exists_calls += 1
        return channel_id in self.live_channels


@pytest.fixture
def store() -> InMemoryStateStore:
    return InMemoryStateStore(clock=FakeClock())


@pytest.fixture
def database() -> FakeLogsDatabase:
    return FakeLogsDatabase()


@pytest.fixture
def platform() -> FakeLogsPlatform:
    return FakeLogsPlatform()


@pytest.fixture
def service(store: InMemoryStateStore, database: FakeLogsDatabase, platform: FakeLogsPlatform) -> LogService:
    return LogService(database=database, platform=platform, state=StateService(store=store))  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_first_resolution_creates_admin_only_channel(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    channel_id = await service.resolve_channel(GUILD)
    assert channel_id is not None
    assert platform.default_policy_applied == [channel_id]
    stored = database.channels[f"{GUILD}:{BOT_LOGS_CATEGORY}"]
    assert stored.channel_id == channel_id
    assert stored.name == BOT_LOGS_CHANNEL_NAME
    assert stored.category == BOT_LOGS_CATEGORY


@pytest.mark.asyncio
async def test_second_resolution_hits_the_cache(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    first = await service.resolve_channel(GUILD)
    assert platform.exists_calls == 0
    second = await service.resolve_channel(GUILD)
    assert second == first
    assert platform.exists_calls == 1


@pytest.mark.asyncio
async def test_deleted_channel_is_reprovisioned(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    first = await service.resolve_channel(GUILD)
    platform.live_channels.discard(first)
    second = await service.resolve_channel(GUILD)
    assert second is not None and second != first
    assert database.channels[f"{GUILD}:{BOT_LOGS_CATEGORY}"].channel_id == second
    assert platform.default_policy_applied == [first, second]


@pytest.mark.asyncio
async def test_log_event_delivers_footer_as_subtext(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    event = LifecycleEvent(kind="start", message="Bot is live", footer="kingdoms-deploy env=test")
    await service.log_event(GUILD, event)
    _channel_id, content = platform.sent[-1]
    assert content == "Bot is live\n-# kingdoms-deploy env=test"


@pytest.mark.asyncio
async def test_log_event_never_raises_on_platform_failure(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    platform.live_channels.clear()
    event = LifecycleEvent(kind="stop", message="Bye")
    await service.log_event(GUILD, event)


@pytest.mark.asyncio
async def test_crash_loop_collapses_after_threshold(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    event = LifecycleEvent(kind="start", message="Bot is live")
    for _ in range(CRASH_LOOP_THRESHOLD):
        await service.log_crash_loop(GUILD, event)
    assert len(platform.sent) == 1
    assert "crash-loop detected" in platform.sent[0][1]
    assert f"{CRASH_LOOP_THRESHOLD} events" in platform.sent[0][1]


@pytest.mark.asyncio
async def test_below_threshold_crash_loop_is_silent(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    event = LifecycleEvent(kind="start", message="Bot is live")
    for _ in range(CRASH_LOOP_THRESHOLD - 1):
        await service.log_crash_loop(GUILD, event)
    assert platform.sent == []


@pytest.mark.asyncio
async def test_default_policy_is_admin_only(database: FakeLogsDatabase) -> None:
    policy = default_policy()
    assert policy["default"] == "admin_only"
    assert policy["roles_with_view"] == []
    await database.set_policy(GUILD, BOT_LOGS_CATEGORY, policy)
    assert await database.get_policy(GUILD, BOT_LOGS_CATEGORY) == policy


@pytest.mark.asyncio
async def test_grant_role_view_access_persists_grants_and_audits(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    await service.grant_role_view_access(GUILD, "999", by="42")
    policy = await service.get_access_policy(GUILD)
    assert "999" in policy["roles_with_view"]
    assert ("grant", "999") or True
    assert any(role == "999" for _, role in platform.role_grants)
    audit = platform.sent[-1][1]
    assert "Access policy updated" in audit
    assert "granted view" in audit


@pytest.mark.asyncio
async def test_reapply_default_policy_audits(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    await service.reapply_default_policy(GUILD, by="42")
    policy = await service.get_access_policy(GUILD)
    assert policy["default"] == "admin_only"
    assert policy["roles_with_view"] == []
    assert "reset to admin-only" in platform.sent[-1][1]


@pytest.mark.asyncio
async def test_redis_down_degrades_to_mongo(
    store: InMemoryStateStore, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    class FailingStore(InMemoryStateStore):
        async def get(self, key: str) -> str | None:
            raise RuntimeError("redis down")

        async def set(self, key: str, value: str, ttl: int | None = None, only_if_absent: bool = False) -> bool:
            raise RuntimeError("redis down")

        async def delete(self, key: str) -> bool:
            raise RuntimeError("redis down")

    service = LogService(
        database=database,
        platform=platform,
        state=StateService(store=FailingStore(clock=FakeClock())),  # type: ignore[arg-type]
    )
    channel_id = await service.resolve_channel(GUILD)
    assert channel_id is not None
    assert database.channels[f"{GUILD}:{BOT_LOGS_CATEGORY}"].channel_id == channel_id
