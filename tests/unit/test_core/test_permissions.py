"""Unit tests for the PermissionService (kingdoms-services#55).

Covers the runtime authorization decision tree: bot-admin bypass,
DM policy (dm_allowed), open actions (no declared roles), the
required-role resolution through the mod-role mappings, and the
fail-closed behavior on lookup errors.
"""

from __future__ import annotations

import pytest

from kingdoms.core.services.permissions import ActionContext, PermissionService

GUILD = "123456"
USER = "42"
ADMIN = "1"


class FakeMembers:
    """In-memory MemberRoles seam: live role ids per member."""

    def __init__(self, roles: dict[str, list[str]] | None = None) -> None:
        self.roles = roles or {}
        self.fail = False

    async def get_member_role_ids(self, guild_id: str, user_id: str) -> list[str]:
        if self.fail:
            raise RuntimeError("store down")
        return self.roles.get(f"{guild_id}:{user_id}", [])


class FakeResolver:
    """In-memory mod-role resolver: role_key -> platform role id."""

    def __init__(self, mappings: dict[str, str] | None = None) -> None:
        self.mappings = mappings or {}
        self.fail = False

    async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
        if self.fail:
            raise RuntimeError("store down")
        return self.mappings.get(f"{guild_id}:{mod}:{role_key}")


def make_service(
    members: FakeMembers | None = None,
    resolver: FakeResolver | None = None,
    bot_admins: tuple[str, ...] = (),
) -> PermissionService:
    return PermissionService(
        members=members or FakeMembers(),
        roles=resolver or FakeResolver(),
        bot_admins=bot_admins,
    )


def ctx(**kwargs: object) -> ActionContext:
    base = {
        "user_id": USER,
        "mod": "clans",
        "custom_id": "clans:join:confirm",
        "guild_id": GUILD,
        "required_roles": (),
        "dm_allowed": False,
    }
    base.update(kwargs)
    return ActionContext(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_open_action_allows_every_guild_member() -> None:
    result = await make_service().is_authorized(ctx())
    assert result.allowed
    assert result.reason == "open"


@pytest.mark.asyncio
async def test_bot_admin_bypasses_mod_level_checks() -> None:
    service = make_service(bot_admins=(ADMIN,))
    result = await service.is_authorized(ctx(user_id=ADMIN, required_roles=("clan_member",)))
    assert result.allowed
    assert result.reason == "bot_admin"


@pytest.mark.asyncio
async def test_dm_interaction_allowed_only_when_declared() -> None:
    service = make_service()
    denied = await service.is_authorized(ctx(guild_id=None, required_roles=("clan_member",)))
    assert not denied.allowed
    assert denied.reason == "dm_not_allowed"
    allowed = await service.is_authorized(ctx(guild_id=None, dm_allowed=True))
    assert allowed.allowed
    assert allowed.reason == "dm_allowed"


@pytest.mark.asyncio
async def test_missing_role_denies() -> None:
    members = FakeMembers({f"{GUILD}:{USER}": ["111"]})
    resolver = FakeResolver({f"{GUILD}:clans:clan_member": "222"})
    result = await make_service(members, resolver).is_authorized(ctx(required_roles=("clan_member",)))
    assert not result.allowed
    assert result.reason == "missing_role"


@pytest.mark.asyncio
async def test_holding_the_role_allows() -> None:
    members = FakeMembers({f"{GUILD}:{USER}": ["222", "111"]})
    resolver = FakeResolver({f"{GUILD}:clans:clan_member": "222"})
    result = await make_service(members, resolver).is_authorized(ctx(required_roles=("clan_member",)))
    assert result.allowed
    assert result.reason == "role"


@pytest.mark.asyncio
async def test_any_of_the_declared_roles_allows() -> None:
    members = FakeMembers({f"{GUILD}:{USER}": ["333"]})
    resolver = FakeResolver({f"{GUILD}:clans:clan_member": "222", f"{GUILD}:clans:clan_leader": "333"})
    service = make_service(members, resolver)
    assert await service.user_has_any_role(GUILD, USER, "clans", ["clan_member", "clan_leader"])
    result = await service.is_authorized(ctx(required_roles=("clan_member", "clan_leader")))
    assert result.allowed


@pytest.mark.asyncio
async def test_unmapped_role_key_denies() -> None:
    members = FakeMembers({f"{GUILD}:{USER}": ["222"]})
    resolver = FakeResolver({})
    result = await make_service(members, resolver).is_authorized(ctx(required_roles=("clan_member",)))
    assert not result.allowed


@pytest.mark.asyncio
async def test_lookup_failure_fails_closed() -> None:
    members = FakeMembers()
    members.fail = True
    resolver = FakeResolver({f"{GUILD}:clans:clan_member": "222"})
    result = await make_service(members, resolver).is_authorized(ctx(required_roles=("clan_member",)))
    assert not result.allowed


@pytest.mark.asyncio
async def test_resolver_failure_fails_closed() -> None:
    members = FakeMembers({f"{GUILD}:{USER}": ["222"]})
    resolver = FakeResolver({f"{GUILD}:clans:clan_member": "222"})
    resolver.fail = True
    result = await make_service(members, resolver).is_authorized(ctx(required_roles=("clan_member",)))
    assert not result.allowed


@pytest.mark.asyncio
async def test_malformed_custom_id_fails_loud() -> None:
    with pytest.raises(ValueError, match="custom_id"):
        ctx(custom_id="join-confirm")


@pytest.mark.asyncio
async def test_result_is_truthy_when_allowed() -> None:
    result = await make_service().is_authorized(ctx())
    assert bool(result) is True
    assert "proceed" if result else "deny"  # usable directly in if-statements
