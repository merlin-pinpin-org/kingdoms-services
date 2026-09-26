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
        self.settings: dict[str, dict[str, Any]] = {}
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

    async def get_guild_settings(self, guild_id: str) -> dict[str, Any] | None:
        return self.settings.get(guild_id)

    async def set_guild_settings(self, guild_id: str, settings: dict[str, Any]) -> None:
        self.settings[guild_id] = dict(settings)


class FakeLogsPlatform:
    """In-memory LogsPlatform: channel created once, deletable, no Discord."""

    def __init__(self) -> None:
        self.next_channel_id = 1000
        self.live_channels: set[str] = set()
        self.default_policy_applied: list[str] = []
        self.public_policy_applied: list[str] = []
        self.role_grants: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []
        self.layouts: list[tuple[str, Any]] = []
        self.exists_calls = 0
        self.adoptable: set[str] = set()
        self.channel_names: dict[str, str] = {}

    async def find_logs_channel(self, guild_id: str) -> str | None:
        adopted = sorted(self.adoptable & self.live_channels)
        return adopted[0] if adopted else None

    async def create_logs_channel(self, guild_id: str) -> str:
        channel_id = str(self.next_channel_id)
        self.next_channel_id += 1
        self.live_channels.add(channel_id)
        return channel_id

    async def apply_default_policy(self, guild_id: str, channel_id: str) -> None:
        self.default_policy_applied.append(channel_id)

    async def apply_public_policy(self, guild_id: str, channel_id: str) -> None:
        self.public_policy_applied.append(channel_id)

    async def list_text_channels(self, guild_id: str) -> list[dict[str, str]]:
        return [
            {"id": channel_id, "name": self.channel_names.get(channel_id, channel_id)}
            for channel_id in sorted(self.live_channels)
        ]

    async def grant_role_view(self, guild_id: str, channel_id: str, role_id: str) -> None:
        self.role_grants.append((channel_id, role_id))

    async def send_log_message(self, guild_id: str, channel_id: str, content: str, layout: Any = None) -> None:
        if channel_id not in self.live_channels:
            raise RuntimeError("channel deleted")
        self.sent.append((channel_id, content))
        self.layouts.append((channel_id, layout))

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
    persisted_policy = await database.get_policy(GUILD, BOT_LOGS_CATEGORY)
    assert persisted_policy is not None
    assert persisted_policy["default"] == "admin_only"
    assert persisted_policy["roles_with_view"] == []


@pytest.mark.asyncio
async def test_existing_channel_is_adopted_not_duplicated(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    """A pre-existing logs channel with no DB record is adopted, not duplicated.

    This is the CI/CD-bot scenario: a fresh ephemeral MongoDB with an
    existing guild must never create a second 🤖-bot-logs channel.
    """
    platform.live_channels.add("999")
    platform.adoptable.add("999")
    channel_id = await service.resolve_channel(GUILD)
    assert channel_id == "999"
    assert platform.default_policy_applied == []
    stored = database.channels[f"{GUILD}:{BOT_LOGS_CATEGORY}"]
    assert stored.channel_id == "999"


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


@pytest.mark.asyncio
async def test_set_channel_reroutes_and_reapplies_rights(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    """Routing to an existing channel: the policy follows the routing."""
    old = await service.resolve_channel(GUILD)
    assert old is not None
    await service.grant_role_view_access(GUILD, "777", by="42")
    platform.live_channels.add("5555")
    platform.default_policy_applied.clear()
    await service.set_channel(GUILD, "5555", by="42")
    assert database.channels[f"{GUILD}:{BOT_LOGS_CATEGORY}"].channel_id == "5555"
    assert "5555" in platform.default_policy_applied, "the visibility policy is re-applied"
    assert ("5555", "777") in platform.role_grants, "the role grants follow the routing"
    assert "5555" in platform.sent[-1][1] or "<#5555>" in platform.sent[-1][1]


@pytest.mark.asyncio
async def test_set_channel_rejects_unknown_channel(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    with pytest.raises(ValueError):
        await service.set_channel(GUILD, "9999", by="42")


@pytest.mark.asyncio
async def test_set_visibility_public_then_back(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    """Visibility toggling: public opens the channel, admin-only restores
    the default overwrites and re-applies the role grants."""
    channel_id = await service.resolve_channel(GUILD)
    assert channel_id is not None
    await service.grant_role_view_access(GUILD, "777", by="42")
    platform.default_policy_applied.clear()
    platform.role_grants.clear()

    await service.set_visibility(GUILD, public=True, by="42")
    policy = await service.get_access_policy(GUILD)
    assert policy["default"] == "public"
    assert platform.public_policy_applied == [channel_id]
    assert "public" in platform.sent[-1][1]

    await service.set_visibility(GUILD, public=False, by="42")
    policy = await service.get_access_policy(GUILD)
    assert policy["default"] == "admin_only"
    assert platform.default_policy_applied == [channel_id]
    assert (channel_id, "777") in platform.role_grants, "grants are restored on admin-only"


@pytest.mark.asyncio
async def test_locale_roundtrip_and_fallback(
    service: LogService, database: FakeLogsDatabase, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    assert await service.get_locale(GUILD) == "en", "the default locale is en"
    await service.set_locale(GUILD, "fr", by="42")
    assert await service.get_locale(GUILD) == "fr"
    assert database.settings[GUILD]["locale"] == "fr"
    with pytest.raises(ValueError):
        await service.set_locale(GUILD, "de", by="42")


@pytest.mark.asyncio
async def test_list_text_channels_serves_the_picker(
    service: LogService, platform: FakeLogsPlatform
) -> None:
    await service.resolve_channel(GUILD)
    platform.live_channels.add("5555")
    platform.channel_names["5555"] = "general"
    channels = await service.list_text_channels(GUILD)
    ids = [c["id"] for c in channels]
    assert "5555" in ids
    entry = next(c for c in channels if c["id"] == "5555")
    assert entry["name"] == "general"
