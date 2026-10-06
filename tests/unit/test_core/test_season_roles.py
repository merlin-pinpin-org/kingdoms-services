"""Tests for the season-roles service: per-season player/staff roles.

The contract: the logical keys are namespaced per season
(player:s1/staff:s1), the display names are formatted from the mod,
and the sync adds/removes the provisioned role.
"""
from __future__ import annotations

import pytest

from kingdoms.core.services.mod_definition import RoleDef
from kingdoms.core.services.season_roles import SeasonRolesService


class _FakeModRoles:
    """ModRolesService stand-in: mappings + assignments, in memory."""

    def __init__(self) -> None:
        self.mappings: dict[str, str] = {}
        self.created: list[str] = []
        self.assigned: list[tuple[str, str, str]] = []
        self.removed: list[tuple[str, str, str]] = []

    async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
        return self.mappings.get(f"{guild_id}:{mod}:{role_key}")

    async def adopt_or_create_role(self, guild_id: str, mod_name: str, role_def: RoleDef) -> str | None:
        role_id = f"role-{len(self.created) + 1}"
        self.created.append(role_def.display_name)
        self.mappings[f"{guild_id}:{mod_name}:{role_def.key}"] = role_id
        return role_id

    async def assign_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        self.assigned.append((user_id, role_id, role_key))

    async def remove_platform_role(self, guild_id: str, user_id: str, role_id: str, mod: str, role_key: str) -> None:
        self.removed.append((user_id, role_id, role_key))


def test_role_key_is_namespaced_per_season_and_kind() -> None:
    """The keys are player:<season> and staff:<season>."""
    service = SeasonRolesService(_FakeModRoles(), "ladder")
    assert service.role_key("player", "s1") == "player:s1"
    assert service.role_key("staff", "s2") == "staff:s2"
    with pytest.raises(ValueError):
        service.role_key("owner", "s1")


def test_display_name_formats_the_mod_and_season() -> None:
    """The display name reads 'Joueur ladder s1' / 'Staff ladder s1'."""
    service = SeasonRolesService(_FakeModRoles(), "ladder")
    assert service.display_name("player", "s1") == "Joueur ladder s1"
    assert service.display_name("staff", "s1") == "Staff ladder s1"


@pytest.mark.asyncio
async def test_sync_player_role_provisions_then_assigns() -> None:
    """A membership sync provisions the role once, then assigns it."""
    mod_roles = _FakeModRoles()
    service = SeasonRolesService(mod_roles, "ladder")
    await service.sync_player_role("42", "777", "s1", member=True)
    assert mod_roles.created == ["Joueur ladder s1"]
    assert mod_roles.assigned == [("777", "role-1", "player")]


@pytest.mark.asyncio
async def test_sync_staff_role_reuses_the_provisioned_role() -> None:
    """A second sync reuses the mapping — the role is created once."""
    mod_roles = _FakeModRoles()
    service = SeasonRolesService(mod_roles, "ladder")
    await service.sync_staff_role("42", "777", "s1", member=True)
    await service.sync_staff_role("42", "888", "s1", member=True)
    assert mod_roles.created == ["Staff ladder s1"]
    assert mod_roles.assigned == [("777", "role-1", "staff"), ("888", "role-1", "staff")]


@pytest.mark.asyncio
async def test_sync_player_role_removal_removes_the_role() -> None:
    """A membership end removes the provisioned role."""
    mod_roles = _FakeModRoles()
    service = SeasonRolesService(mod_roles, "ladder")
    await service.sync_player_role("42", "777", "s1", member=True)
    await service.sync_player_role("42", "777", "s1", member=False)
    assert mod_roles.removed == [("777", "role-1", "player")]


def test_season_label_reads_the_short_label() -> None:
    """The label extraction prefers label, then name, then the id."""
    from kingdoms.core.services.season_roles import season_label

    assert season_label("s1") == "s1"
    assert season_label({"label": "s2", "name": "Season 2"}) == "s2"
    assert season_label({"name": "Season 2"}) == "Season 2"
    assert season_label({"season_id": "abc"}) == "abc"
