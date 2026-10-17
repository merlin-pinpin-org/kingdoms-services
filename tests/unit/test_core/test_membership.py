"""Tests for the core membership service: the generic register/unregister.

The contract: the mod hooks own the registration business, the core
orchestrates (hooks → season role sync), a seasonal mod syncs its
player role on every change, a non-seasonal mod skips the role sync.
"""

from __future__ import annotations

import pytest

from kingdoms.core.services.membership import MembershipResult, MembershipService


class _RecordingHooks:
    """MembershipHooks stand-in recording the calls."""

    def __init__(self) -> None:
        self.registrations: list[tuple[str, str, str]] = []
        self.unregistrations: list[str] = []

    async def register_member(self, guild_id: str, user_id: str, display_name: str) -> MembershipResult:
        self.registrations.append((guild_id, user_id, display_name))
        return MembershipResult(ok=True, summary="registered")

    async def unregister_member(self, guild_id: str, user_id: str) -> MembershipResult:
        self.unregistrations.append(user_id)
        return MembershipResult(ok=False, summary="not registered")


class _FixedSeasons:
    """MembershipSeasons stand-in: one fixed label."""

    def __init__(self, label: str | None) -> None:
        self._label = label

    async def active_season(self, guild_id: str) -> str | None:
        return self._label


class _RecordingSeasonRoles:
    """SeasonRolesService stand-in recording the syncs."""

    def __init__(self) -> None:
        self.syncs: list[tuple[str, str, str, bool]] = []

    async def sync_player_role(self, guild_id: str, user_id: str, season: str, member: bool) -> None:
        self.syncs.append((guild_id, user_id, season, member))


@pytest.mark.asyncio
async def test_register_runs_the_hooks_then_syncs_the_role() -> None:
    """A seasonal registration: hooks first, then the player role sync."""
    hooks, roles = _RecordingHooks(), _RecordingSeasonRoles()
    service = MembershipService("ladder", hooks, _FixedSeasons("s1"), roles)
    result = await service.register("42", "777", "Alice")
    assert result.ok is True
    assert hooks.registrations == [("42", "777", "Alice")]
    assert roles.syncs == [("42", "777", "s1", True)]


@pytest.mark.asyncio
async def test_unregister_syncs_the_removal_even_on_failure() -> None:
    """The role sync follows the hooks' outcome, ok or not."""
    hooks, roles = _RecordingHooks(), _RecordingSeasonRoles()
    service = MembershipService("ladder", hooks, _FixedSeasons("s1"), roles)
    result = await service.unregister("42", "777")
    assert result.ok is False
    assert roles.syncs == [("42", "777", "s1", False)]


@pytest.mark.asyncio
async def test_non_seasonal_mod_skips_the_role_sync() -> None:
    """Without a season provider, no role is ever synced."""
    hooks, roles = _RecordingHooks(), _RecordingSeasonRoles()
    service = MembershipService("groups", hooks, None, roles)
    await service.register("42", "777", "Alice")
    assert roles.syncs == []


@pytest.mark.asyncio
async def test_no_active_season_skips_the_role_sync() -> None:
    """A seasonal mod with no active season syncs nothing."""
    hooks, roles = _RecordingHooks(), _RecordingSeasonRoles()
    service = MembershipService("ladder", hooks, _FixedSeasons(None), roles)
    await service.register("42", "777", "Alice")
    assert roles.syncs == []


@pytest.mark.asyncio
async def test_role_sync_failure_never_breaks_registration() -> None:
    """A failing role sync leaves the registration result intact."""
    hooks = _RecordingHooks()

    class _BrokenRoles:
        async def sync_player_role(self, guild_id: str, user_id: str, season: str, member: bool) -> None:
            raise RuntimeError("role sync down")

    service = MembershipService("ladder", hooks, _FixedSeasons("s1"), _BrokenRoles())
    result = await service.register("42", "777", "Alice")
    assert result.ok is True


def test_mod_name_is_exposed() -> None:
    """The membership knows its mod."""
    service = MembershipService("ladder", _RecordingHooks())
    assert service.mod_name == "ladder"
