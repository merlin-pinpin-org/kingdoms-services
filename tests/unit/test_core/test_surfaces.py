"""Unit tests for the SurfaceService (kingdoms-services#130).

Covers mod-scoped surface resolution (delegated to the ChannelService
seam) and surface permission queries (delegated to the PermissionService
seam), including failure degradation.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.surfaces import SurfaceService


class FakeChannels:
    """In-memory SurfaceChannels: records the resolved category keys."""

    def __init__(self) -> None:
        self.resolved: list[str] = []

    async def get_channel_for_category(self, guild_id: str, category: str) -> Any:
        self.resolved.append(f"{guild_id}:{category}")
        return _FakeChannel(f"ch-{category}")


class _FakeChannel:
    def __init__(self, id: str) -> None:
        self.id = id
        self.name = id


class FakePermissions:
    """In-memory SurfacePermissions: role grants, with a failure switch."""

    def __init__(self) -> None:
        self.granted: set[tuple[str, str, str, str]] = set()
        self.fail = False

    async def user_has_role(self, guild_id: str, user_id: str, mod: str, role_key: str) -> bool:
        if self.fail:
            raise RuntimeError("permission store down")
        return (guild_id, user_id, mod, role_key) in self.granted


def make_service() -> tuple[SurfaceService, FakeChannels, FakePermissions]:
    channels = FakeChannels()
    permissions = FakePermissions()
    return SurfaceService(channels=channels, permissions=permissions), channels, permissions


class TestSurfaces:
    async def test_surface_channel_resolves_the_mod_scoped_key(self) -> None:
        service, channels, _perms = make_service()
        channel = await service.surface_channel("g1", "ladder", "leaderboard")
        assert channels.resolved == ["g1:ladder:leaderboard"]
        assert channel.id == "ch-ladder:leaderboard"

    async def test_each_surface_is_resolved_independently(self) -> None:
        service, channels, _perms = make_service()
        for surface in ("play", "leaderboard", "matches", "players", "admins"):
            await service.surface_channel("g1", "ladder", surface)
        assert channels.resolved == [f"g1:ladder:{s}" for s in ("play", "leaderboard", "matches", "players", "admins")]


class TestSurfacePermissions:
    async def test_role_granted_answers_true(self) -> None:
        service, _channels, perms = make_service()
        perms.granted.add(("g1", "u1", "ladder", "admin"))
        assert await service.has_surface_role("g1", "u1", "ladder", "admin") is True

    async def test_role_absent_answers_false(self) -> None:
        service, _channels, _perms = make_service()
        assert await service.has_surface_role("g1", "u1", "ladder", "admin") is False

    async def test_lookup_failure_denies_never_opens(self) -> None:
        service, _channels, perms = make_service()
        perms.fail = True
        assert await service.has_surface_role("g1", "u1", "ladder", "admin") is False
