"""Unit tests for the ModRolesService (kingdoms-services#26).

Covers the generic per-mod role provisioning (mapping persistence,
adoption of same-name roles, creation when missing), the logical-key
resolution order (cache -> mapping -> declared display name), the admin
rebinding, and member assignment/removal through the narrow seam.
"""

from __future__ import annotations

from kingdoms.core.models.role_mapping import RoleMappingModel
from kingdoms.core.services.mod_definition import ModDefinition, RoleDef
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.roles import ModRolesService
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore

GUILD = "123456"
USER = "42"


class FakeRolesDatabase:
    """In-memory RolesDatabase: mappings keyed by guild:mod:role_key."""

    def __init__(self) -> None:
        self.mappings: dict[str, RoleMappingModel] = {}
        self.upserts: list[RoleMappingModel] = []
        self.deletes: list[str] = []

    async def find_role_mapping(self, guild_id: str, mod: str, role_key: str) -> RoleMappingModel | None:
        return self.mappings.get(f"{guild_id}:{mod}:{role_key}")

    async def upsert_role_mapping(self, mapping: RoleMappingModel) -> None:
        self.mappings[mapping.id] = mapping
        self.upserts.append(mapping)

    async def delete_role_mapping(self, guild_id: str, mod: str, role_key: str) -> bool:
        existed = f"{guild_id}:{mod}:{role_key}" in self.mappings
        if existed:
            self.deletes.append(f"{guild_id}:{mod}:{role_key}")
            del self.mappings[f"{guild_id}:{mod}:{role_key}"]
        return existed


class FakeRolesPlatform:
    """In-memory RolesPlatform: roles by name, ids sequential."""

    def __init__(self) -> None:
        self.next_id = 3000
        self.roles_by_name: dict[str, str] = {}
        self.created: list[str] = []

    async def find_role_by_name(self, guild_id: str, name: str) -> str | None:
        return self.roles_by_name.get(name)

    async def create_role(self, guild_id: str, name: str, reason: str) -> str:
        role_id = f"role{self.next_id}"
        self.next_id += 1
        self.roles_by_name[name] = role_id
        self.created.append(name)
        return role_id


class FakeMembersPlatform:
    """In-memory ModRolesPlatform: assignment ledger."""

    def __init__(self) -> None:
        self.added: list[tuple[str, str, str]] = []
        self.removed: list[tuple[str, str, str]] = []

    async def add_role_to_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        self.added.append((user_id, role_id, reason))

    async def remove_role_from_member(self, guild_id: str, user_id: str, role_id: str, reason: str) -> None:
        self.removed.append((user_id, role_id, reason))


def make_service() -> tuple[
    ModRolesService, FakeRolesDatabase, FakeRolesPlatform, FakeMembersPlatform, InMemoryStateStore
]:
    """Build a ModRolesService with in-memory fakes and a real StateService."""
    db = FakeRolesDatabase()
    platform = FakeRolesPlatform()
    members = FakeMembersPlatform()
    registry = ModRegistry()
    registry.register(
        ModDefinition(
            name="clans",
            roles=(
                RoleDef(key="clan_leader", display_name="Clan Leader"),
                RoleDef(key="clan_member", display_name="Clan Member"),
            ),
        )
    )
    store = InMemoryStateStore(clock=FakeClock())
    service = ModRolesService(
        database=db,
        platform=platform,
        registry=registry,
        members=members,
        cache=StateService(store),
    )
    return service, db, platform, members, store


class TestSetupModRoles:
    async def test_provisions_every_declared_role_and_persists_mappings(self) -> None:
        service, db, platform, _members, _store = make_service()
        resolved = await service.setup_mod_roles(GUILD, "clans")
        assert set(resolved) == {"clan_leader", "clan_member"}
        assert platform.created == ["Clan Leader", "Clan Member"]
        assert db.mappings[f"{GUILD}:clans:clan_leader"].role_id == resolved["clan_leader"]

    async def test_idempotent_second_setup_reuses_the_mappings(self) -> None:
        service, db, platform, _members, _store = make_service()
        first = await service.setup_mod_roles(GUILD, "clans")
        platform.created.clear()
        db.upserts.clear()
        second = await service.setup_mod_roles(GUILD, "clans")
        assert second == first
        assert platform.created == []
        assert db.upserts == []

    async def test_adopts_an_existing_role_by_display_name(self) -> None:
        service, db, platform, _members, _store = make_service()
        platform.roles_by_name["Clan Leader"] = "role-existing"
        resolved = await service.setup_mod_roles(GUILD, "clans")
        assert resolved["clan_leader"] == "role-existing"
        assert db.mappings[f"{GUILD}:clans:clan_leader"].role_id == "role-existing"

    async def test_unknown_mod_fails_loudly(self) -> None:
        service, _db, _platform, _members, _store = make_service()
        try:
            await service.setup_mod_roles(GUILD, "unknown")
        except KeyError as error:
            assert "unknown" in str(error)
        else:
            raise AssertionError("expected KeyError")


class TestResolveAndBind:
    async def test_resolution_order_cache_mapping_display_name(self) -> None:
        service, db, platform, _members, _store = make_service()
        await service.setup_mod_roles(GUILD, "clans")
        db.mappings.clear()
        resolved = await service.resolve_role_id(GUILD, "clans", "clan_leader")
        assert resolved == platform.roles_by_name["Clan Leader"]

    async def test_bind_role_rebinds_to_an_existing_platform_role(self) -> None:
        service, db, _platform, _members, _store = make_service()
        await service.bind_role(GUILD, "clans", "clan_leader", "role-custom")
        assert db.mappings[f"{GUILD}:clans:clan_leader"].role_id == "role-custom"
        assert await service.resolve_role_id(GUILD, "clans", "clan_leader") == "role-custom"

    async def test_resolve_without_mapping_or_role_returns_none(self) -> None:
        service, _db, _platform, _members, _store = make_service()
        assert await service.resolve_role_id(GUILD, "clans", "clan_leader") is None

    async def test_undeclared_role_key_fails_loudly(self) -> None:
        service, _db, _platform, _members, _store = make_service()
        try:
            await service.resolve_role_id(GUILD, "clans", "undeclared")
        except KeyError as error:
            assert "does not declare" in str(error)
        else:
            raise AssertionError("expected KeyError")


class TestAssignRemove:
    async def test_assign_and_remove_through_the_mapping(self) -> None:
        service, _db, _platform, members, _store = make_service()
        await service.setup_mod_roles(GUILD, "clans")
        await service.assign_mod_role(GUILD, USER, "clans", "clan_member")
        user, role_id, reason = members.added[0]
        assert user == USER
        assert reason == "kingdoms: assign mod role clans:clan_member"
        await service.remove_mod_role(GUILD, USER, "clans", "clan_member")
        assert members.removed[0][1] == role_id

    async def test_assign_without_resolution_raises_lookup_error(self) -> None:
        service, _db, _platform, _members, _store = make_service()
        try:
            await service.assign_mod_role(GUILD, USER, "clans", "clan_member")
        except LookupError as error:
            assert "clans:clan_member" in str(error)
        else:
            raise AssertionError("expected LookupError")

    async def test_remove_without_resolution_is_a_no_op(self) -> None:
        service, _db, _platform, members, _store = make_service()
        await service.remove_mod_role(GUILD, USER, "clans", "clan_member")
        assert members.removed == []
