"""Unit tests for the IdentityService (kingdoms-services#130).

Covers platform-account → user_id binding, idempotent user creation,
cache-aside behaviour and cache-failure degradation.
"""

from __future__ import annotations

from kingdoms.core.models.user import UserModel
from kingdoms.core.services.identity import IdentityService
from kingdoms.core.services.state import StateService
from tests.mocks.state_mock import FakeClock, InMemoryStateStore


class FakeIdentityDatabase:
    """In-memory IdentityDatabase: identities and users."""

    def __init__(self) -> None:
        self.identities: dict[str, str] = {}
        self.users: dict[str, UserModel] = {}
        self.binds: list[tuple[str, str, str]] = []
        self.user_upserts: list[UserModel] = []

    async def find_identity(self, platform: str, platform_user_id: str) -> str | None:
        return self.identities.get(f"{platform}:{platform_user_id}")

    async def bind_identity(self, platform: str, platform_user_id: str, user_id: str) -> None:
        self.identities[f"{platform}:{platform_user_id}"] = user_id
        self.binds.append((platform, platform_user_id, user_id))

    async def unbind_identity(self, platform: str, platform_user_id: str) -> bool:
        key = f"{platform}:{platform_user_id}"
        existed = key in self.identities
        if existed:
            del self.identities[key]
        return existed

    async def find_user(self, user_id: str) -> UserModel | None:
        return self.users.get(user_id)

    async def upsert_user(self, user: UserModel) -> None:
        self.users[user.id] = user
        self.user_upserts.append(user)


class BrokenCache:
    """A cache seam that always fails — the service must degrade."""

    async def get_state(self, scope: str, key: str) -> dict[str, object] | None:
        raise RuntimeError("cache down")

    async def set_state(self, scope: str, key: str, value: dict[str, object], ttl: int) -> None:
        raise RuntimeError("cache down")

    async def delete_state(self, scope: str, key: str) -> bool:
        raise RuntimeError("cache down")


def make_service(cache: object | None = None) -> tuple[IdentityService, FakeIdentityDatabase]:
    """Build the service with an in-memory database and a cache."""
    db = FakeIdentityDatabase()
    if cache is None:
        clock = FakeClock()
        cache = StateService(InMemoryStateStore(clock=clock))
    return IdentityService(database=db, cache=cache), db


class TestResolveUserId:
    async def test_unknown_account_resolves_to_none(self) -> None:
        service, _db = make_service()
        assert await service.resolve_user_id("discord", "100") is None

    async def test_get_or_create_binds_and_creates_the_user(self) -> None:
        service, db = make_service()
        user = await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        assert user.id == "discord:100"
        assert user.platform == "discord"
        assert user.display_name == "Aelis"
        assert db.identities["discord:100"] == "discord:100"
        assert db.users["discord:100"].display_name == "Aelis"

    async def test_get_or_create_is_idempotent(self) -> None:
        service, db = make_service()
        first = await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        second = await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis2")
        assert second.id == first.id
        assert len(db.user_upserts) == 1
        assert len(db.binds) == 1

    async def test_identities_are_per_platform(self) -> None:
        service, db = make_service()
        await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        other = await service.get_or_create_user(platform="other", platform_user_id="100", display_name="Aelis")
        assert other.id == "other:100"
        assert db.identities["discord:100"] != db.identities["other:100"]

    async def test_resolve_after_creation(self) -> None:
        service, _db = make_service()
        await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        assert await service.resolve_user_id("discord", "100") == "discord:100"

    async def test_broken_cache_degrades_to_the_database(self) -> None:
        service, db = make_service(cache=BrokenCache())
        user = await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        assert user.id == "discord:100"
        assert db.identities["discord:100"] == "discord:100"
        assert await service.resolve_user_id("discord", "100") == "discord:100"


class TestGetUser:
    async def test_get_user_returns_the_document(self) -> None:
        service, _db = make_service()
        user = await service.get_or_create_user(platform="discord", platform_user_id="100", display_name="Aelis")
        found = await service.get_user("discord:100")
        assert found is not None
        assert found.id == user.id

    async def test_get_user_absent_returns_none(self) -> None:
        service, _db = make_service()
        assert await service.get_user("nobody") is None
