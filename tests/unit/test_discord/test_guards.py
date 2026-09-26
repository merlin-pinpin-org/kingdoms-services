"""Tests for the runtime permission guards (kingdoms-services#115).

The developer-mandated rule: **seeing a button never implies being
allowed to click it** — every interactive item behind a privilege is
validated at click time, against the live guild state.
"""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.roles import RolesService
from kingdoms.discord.guards import is_admin, require_admin
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockUser


class _FakeRolesPlatform:
    """Roles platform stand-in: one role id per guild, None when absent."""

    def __init__(self, role_ids: dict[str, str]) -> None:
        self.role_ids = role_ids
        self.lookups: list[tuple[str, str]] = []

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        self.lookups.append((guild_id, name))
        return self.role_ids.get(guild_id)

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        raise AssertionError("not expected in these tests")


class _Member(MockUser):
    """A user carrying live guild roles (the guard reads member.roles)."""

    def __init__(self, user_id: int, roles: list[Any]) -> None:
        super().__init__(id=user_id)
        self.roles = roles


class _Role:
    """A bare role record: an id is all the guard reads."""

    def __init__(self, role_id: int) -> None:
        self.id = role_id


def _interaction(user: MockUser, guild: MockGuild | None = None) -> MockInteraction:
    interaction = MockInteraction(user=user, guild=guild)
    interaction.guild_id = guild.id if guild is not None else None
    return interaction


@pytest.mark.asyncio
async def test_bot_operator_passes_without_lookup() -> None:
    platform = _FakeRolesPlatform({})
    service = RolesService(platform=platform)
    interaction = _interaction(MockUser(id=42), MockGuild(id=1))
    assert await is_admin(interaction, ("42",), service) is True
    assert platform.lookups == []


@pytest.mark.asyncio
async def test_bot_admins_role_member_passes_at_click_time() -> None:
    platform = _FakeRolesPlatform({"1": "777"})
    service = RolesService(platform=platform)
    member = _Member(42, [_Role(777)])
    interaction = _interaction(member, MockGuild(id=1))
    assert await is_admin(interaction, (), service) is True


@pytest.mark.asyncio
async def test_member_without_the_role_is_denied() -> None:
    platform = _FakeRolesPlatform({"1": "777"})
    service = RolesService(platform=platform)
    member = _Member(42, [_Role(888)])
    interaction = _interaction(member, MockGuild(id=1))
    assert await is_admin(interaction, (), service) is False
    assert interaction.response.sent is False


@pytest.mark.asyncio
async def test_role_resolution_failure_denies_closed() -> None:
    class _Boom(_FakeRolesPlatform):
        async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
            raise RuntimeError("discord down")

    service = RolesService(platform=_Boom({}))
    member = _Member(42, [_Role(777)])
    interaction = _interaction(member, MockGuild(id=1))
    assert await is_admin(interaction, (), service) is False


@pytest.mark.asyncio
async def test_require_admin_denies_ephemeral_and_returns_false() -> None:
    service = RolesService(platform=_FakeRolesPlatform({}))
    interaction = _interaction(MockUser(id=99), MockGuild(id=1))
    assert await require_admin(interaction, ("42",), service) is False
    assert interaction.response.sent is True
    content = (interaction.response.message or "").content
    assert "not allowed" in content


@pytest.mark.asyncio
async def test_require_admin_passes_silently_for_operators() -> None:
    service = RolesService(platform=_FakeRolesPlatform({}))
    interaction = _interaction(MockUser(id=42), MockGuild(id=1))
    assert await require_admin(interaction, ("42",), service) is True
    assert interaction.response.sent is False
