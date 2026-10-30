"""Kingdoms ejection-cleanup-v1 — a departed player returns to the newcomer state.

Drasah's beta report (2026-10-30): an ejected lord kept his member
overwrite on the ex-kingdom's category and still read its salons
while ruling another kingdom — the realm re-syncs are replace-edits
that only run on launch/approval, so nothing ever removed the stale
allow. The leave seam now strips the overwrites AND the player roles;
the provisioning sweep (and every blanket realm re-sync) heals the
pre-patch ghosts.
"""

from __future__ import annotations

from typing import Any

import discord
import pytest

from kingdoms.mods.kingdoms.service import KING_ROLE, KingdomsService
from tests.mocks.discord_mock import MockGuild, MockMember, MockRole

from .test_kingdoms_events import MemoryStore, _config

pytestmark = pytest.mark.asyncio


def _noop(*_args: Any, **_kwargs: Any) -> Any:
    async def _f(*args: Any, **kwargs: Any) -> None:
        return None

    return _f()


async def test_leave_returns_the_departed_to_the_newcomer_state(monkeypatch: Any) -> None:
    """The ejection seam strips the departed member overwrites (category
    AND salons) and the Kingdoms Lord role — the other members, the bot
    allow and the Admin staff role all survive."""
    from kingdoms.mods.kingdoms import kingdom_persistent as persistent
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        _apply_player_leave_safe,
        register_kingdoms_panel_wiring,
    )

    service = KingdomsService(MemoryStore(), _config())
    register_kingdoms_panel_wiring(KingdomsPanelWiring(kingdoms_service=service))
    await service.launch(["Aquitaine"])
    await service.enroll_king_awaiting_kingdom("111", "King A")
    await service.assign("111", "Aquitaine", KING_ROLE)

    guild = MockGuild()
    lord_role = MockRole(name="Kingdoms Lord")
    admin_role = MockRole(name="Kingdoms Admin")
    guild._roles[lord_role.id] = lord_role
    guild._roles[admin_role.id] = admin_role
    category = await guild.create_category("🏰 Aquitaine")
    salon = await guild.create_text_channel("🏰 le-royaume", category=category)
    departed = MockMember(id=111, name="king-a", roles=[lord_role, admin_role])
    other = MockMember(id=222, name="other", roles=[lord_role])
    guild.add_member(departed)
    guild.add_member(other)

    # the live state: member allows on the category (the departed one is
    # the ghost-to-be), the bot keeps its own allow
    for target in (departed, other, guild.me):
        await category.set_permissions(
            target, overwrite=discord.PermissionOverwrite(view_channel=True)
        )
    await salon.set_permissions(
        departed, overwrite=discord.PermissionOverwrite(view_channel=True)
    )

    for name in (
        "_send_king_claim_request",
        "_notify_heirs_safe",
        "_refresh_realm_views_for_kingdom_safe",
        "_refresh_realms_panel_safe",
    ):
        monkeypatch.setattr(persistent, name, _noop())

    assert await _apply_player_leave_safe(guild, "111", "admin eject", "fr") is True

    # the departed member overwrite is gone (category AND salon)
    assert category.permission_overwrite_for(departed) is None
    assert salon.permission_overwrite_for(departed) is None
    # the other member and the bot survive
    assert category.permission_overwrite_for(other) is not None
    assert category.permission_overwrite_for(guild.me) is not None
    # the Lord role died, the Admin staff role survived
    assert lord_role not in departed.roles
    assert admin_role in departed.roles
    # the other member kept his role
    assert lord_role in other.roles
    # the data still records the departure (the leave flow stays intact)
    lord = next(item for item in await service.lords() if item.id == "111")
    assert lord.left is True


async def test_sweep_heals_the_departed_ghosts_and_spares_the_living() -> None:
    """The self-heal sweep: a stale member allow of a non-active id dies,
    an active lord's tavern DENY survives, the bot allow and the role
    overwrites survive, and a converged pass costs zero strip."""
    from kingdoms.mods.kingdoms.kingdom_realms import sweep_departed_member_overwrites

    service = KingdomsService(MemoryStore(), _config())
    await service.launch(["Aquitaine"])
    await service.enroll_king_awaiting_kingdom("222", "King B")
    await service.assign("222", "Aquitaine", KING_ROLE)

    guild = MockGuild()
    lord_role = MockRole(name="Kingdoms Lord")
    guild._roles[lord_role.id] = lord_role
    category = await guild.create_category("🏰 Aquitaine")
    living = MockMember(id=222, name="king-b", roles=[lord_role])
    ghost = MockMember(id=333, name="ejected", roles=[lord_role])
    guild.add_member(living)
    guild.add_member(ghost)

    # the tavern insider's member DENY (an ACTIVE lord) and the ghost's
    # stale allow: only the second one must die
    await category.set_permissions(
        living, overwrite=discord.PermissionOverwrite(view_channel=False)
    )
    await category.set_permissions(
        ghost, overwrite=discord.PermissionOverwrite(view_channel=True)
    )
    await category.set_permissions(
        lord_role, overwrite=discord.PermissionOverwrite(view_channel=True)
    )
    await category.set_permissions(
        guild.me, overwrite=discord.PermissionOverwrite(view_channel=True)
    )

    assert await sweep_departed_member_overwrites(guild, service) == 1
    assert category.permission_overwrite_for(ghost) is None
    assert category.permission_overwrite_for(living) is not None
    assert category.permission_overwrite_for(lord_role) is not None
    assert category.permission_overwrite_for(guild.me) is not None
    # converged: a second pass strips nothing
    assert await sweep_departed_member_overwrites(guild, service) == 0


async def test_sweep_is_silent_without_service_or_guild() -> None:
    """The guards: no guild or no service reads nothing and returns 0."""
    from kingdoms.mods.kingdoms.kingdom_realms import sweep_departed_member_overwrites

    assert await sweep_departed_member_overwrites(None, None) == 0
    assert await sweep_departed_member_overwrites(MockGuild(), None) == 0
