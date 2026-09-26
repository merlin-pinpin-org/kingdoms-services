"""Tests for the admin channel service (kingdoms-services#115).

The transparency contract: the 🛡-bot-admins channel exists, the
BOT_ADMINS are synced into the bot-admins role, and the channel
policy is reapplied at every resolution — while the operators'
privileges never depend on the role (the guards check BOT_ADMINS
first).
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.roles import RolesService


class _FakeRolesPlatform:
    """Roles platform stand-in: a fixed role id, counted lookups."""

    def __init__(self) -> None:
        self.created: list[str] = []

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        return "777" if self.created else None

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        self.created.append(name)
        return "777"


class _FakeChannelPlatform:
    """Admin channel platform stand-in recording every call."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.synced: list[tuple[str, str, tuple[str, ...]]] = []
        self.policies: list[tuple[str, str, str]] = []
        self.exists = True

    async def find_channel_by_name(self, guild_id: str, name: str) -> str | None:
        return None

    async def create_channel(self, guild_id: str, name: str, reason: str) -> str:
        self.created.append(name)
        return "555"

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        return self.exists

    async def sync_admin_role_members(self, guild_id: str, role_id: str, admin_ids: tuple[str, ...]) -> None:
        self.synced.append((guild_id, role_id, admin_ids))

    async def apply_channel_policy(self, guild_id: str, channel_id: str, role_id: str) -> None:
        self.policies.append((guild_id, channel_id, role_id))

    async def send_layout(self, guild_id: str, channel_id: str, layout: Any) -> str:
        return "999"


class _FakeDatabase:
    """Persistence stand-in: in-memory channel documents."""

    def __init__(self) -> None:
        self.docs: dict[str, Any] = {}

    async def find_channel(self, guild_id: str, category: str) -> Any:
        return self.docs.get(f"{guild_id}:{category}")

    async def upsert_channel(self, channel: Any) -> None:
        self.docs[channel.id] = channel

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        return self.docs.pop(f"{guild_id}:{category}", None) is not None


@pytest.mark.asyncio
async def test_resolve_provisions_channel_role_and_transparency() -> None:
    platform = _FakeChannelPlatform()
    roles_platform = _FakeRolesPlatform()
    service = AdminChannelService(
        platform=platform,
        database=_FakeDatabase(),
        roles_service=RolesService(platform=roles_platform),
    )
    channel_id = await service.resolve_channel("42", admin_ids=("111", "222"))
    assert channel_id == "555"
    assert platform.created == ["🛡-bot-admins"]
    assert roles_platform.created == ["bot-admins"]
    assert platform.synced == [("42", "777", ("111", "222"))]
    assert platform.policies == [("42", "555", "777")]


@pytest.mark.asyncio
async def test_resolution_refreshes_transparency_every_time() -> None:
    """A manual un-sync never survives a resolution (visible, not hidden)."""
    platform = _FakeChannelPlatform()
    service = AdminChannelService(
        platform=platform,
        database=_FakeDatabase(),
        roles_service=RolesService(platform=_FakeRolesPlatform()),
    )
    await service.resolve_channel("42", admin_ids=("111",))
    await service.resolve_channel("42", admin_ids=("111",))
    assert len(platform.synced) == 2
    assert len(platform.policies) == 2


@pytest.mark.asyncio
async def test_privileges_never_depend_on_the_role() -> None:
    """The transparency sync is one-way: stripping the role costs visibility, not rights.

    The guards read BOT_ADMINS first (environment-sourced); the role
    sync here adds, never removes — an operator removed from the role
    keeps every privilege.
    """
    platform = _FakeChannelPlatform()
    service = AdminChannelService(
        platform=platform,
        database=_FakeDatabase(),
        roles_service=RolesService(platform=_FakeRolesPlatform()),
    )
    await service.resolve_channel("42", admin_ids=("111", "222"))
    # A later resolution with a smaller roster never removes anyone:
    # sync_admin_role_members only adds (the platform contract).
    assert platform.synced[-1] == ("42", "777", ("111", "222"))


@pytest.mark.asyncio
async def test_deliver_posts_the_layout_and_returns_the_message_id() -> None:
    platform = _FakeChannelPlatform()
    service = AdminChannelService(
        platform=platform,
        database=_FakeDatabase(),
        roles_service=RolesService(platform=_FakeRolesPlatform()),
    )
    message_id = await service.deliver("42", object(), admin_ids=("111",))
    assert message_id == "999"
