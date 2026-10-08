"""GuildAccessService tests: default-inactive, request/approve/deny/revoke."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.guild_access import GuildAccessError, GuildAccessService

GAMES = ("aoe2",)
MODS = ("kingdoms", "ladder")


class FakeGuildAccessDB:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def get_guild_access(self, guild_id: str) -> dict[str, Any] | None:
        return self.docs.get(guild_id)

    async def upsert_guild_access(self, document: dict[str, Any]) -> None:
        self.docs[str(document["guild_id"])] = {k: v for k, v in document.items() if k != "_id"}

    async def list_guild_access(self) -> list[dict[str, Any]]:
        return list(self.docs.values())


def _service(db: FakeGuildAccessDB | None = None) -> GuildAccessService:
    return GuildAccessService(db or FakeGuildAccessDB(), games=GAMES, mods=MODS)


async def test_default_nothing_active() -> None:
    service = _service()
    access = await service.get("123")
    assert access["games"] == []
    assert access["mods"] == []
    assert await service.enabled_games("123") == ()
    assert await service.enabled_mods("123") == ()


async def test_request_then_approve_grants_access() -> None:
    service = _service()
    await service.request_access("123", ["game:aoe2", "mod:kingdoms"])
    requests = await service.pending_requests()
    assert len(requests) == 1
    assert requests[0]["keys"] == ["game:aoe2", "mod:kingdoms"]
    await service.approve("123", requests[0]["requested_at"])
    assert await service.enabled_games("123") == ("aoe2",)
    assert await service.enabled_mods("123") == ("kingdoms",)
    assert await service.pending_requests() == []


async def test_deny_drops_request_without_grant() -> None:
    service = _service()
    await service.request_access("123", ["game:aoe2"])
    [request] = await service.pending_requests()
    await service.deny("123", request["requested_at"])
    assert await service.enabled_games("123") == ()
    assert await service.pending_requests() == []


async def test_revoke_removes_grant() -> None:
    service = _service()
    await service.request_access("123", ["game:aoe2"])
    [request] = await service.pending_requests()
    await service.approve("123", request["requested_at"])
    await service.revoke("123", "game:aoe2")
    assert await service.enabled_games("123") == ()


async def test_duplicate_request_is_refused() -> None:
    service = _service()
    await service.request_access("123", ["game:aoe2"])
    [request] = await service.pending_requests()
    await service.approve("123", request["requested_at"])
    try:
        await service.request_access("123", ["game:aoe2"])
        raise AssertionError("expected GuildAccessError")
    except GuildAccessError:
        pass


async def test_unknown_key_is_refused() -> None:
    service = _service()
    try:
        await service.request_access("123", ["game:starcraft"])
        raise AssertionError("expected GuildAccessError")
    except GuildAccessError:
        pass
