"""GuildAccessService tests: default-open catalog, request/approve/deny/revoke."""

from __future__ import annotations

from typing import Any

from botforge.core.services.guild_access import GuildAccessError, GuildAccessService

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


async def test_default_follows_the_declaration_flags() -> None:
    """A guild with no access doc gets the DEFAULT-ACTIVE sets.

    Games default active, mods default inactive (owner direction
    2026-11-11) — the declaration's default_active flag, distinct from
    private (visibility). The synthetic state materializes the defaults
    so the write paths persist a complete document on first touch.
    """
    service = _service()
    access = await service.get("123")
    assert access["games"] == list(GAMES)
    assert access["mods"] == []
    assert await service.enabled_games("123") == GAMES  # default-active games
    assert await service.enabled_mods("123") == ()  # no default-active mod


async def test_an_explicit_empty_doc_sticks() -> None:
    """A stored empty list means NONE active — an uninstall sticks.

    The catalog's Install button is what brings the item back; the
    empty doc must NOT silently re-open the defaults.
    """
    db = FakeGuildAccessDB()
    db.docs["123"] = {"guild_id": "123", "games": [], "mods": [], "pending": {}}
    service = _service(db)
    assert await service.enabled_games("123") == ()
    assert await service.enabled_mods("123") == ()


async def test_request_then_approve_grants_access() -> None:
    """A mod request is approved; the default-active games ride along.

    The approval materializes the document: games default-active set +
    the approved mod (the doc is the whole truth once written).
    """
    service = _service()
    await service.request_access("123", ["mod:kingdoms"])
    requests = await service.pending_requests()
    assert len(requests) == 1
    assert requests[0]["keys"] == ["mod:kingdoms"]
    await service.approve("123", requests[0]["requested_at"])
    assert await service.enabled_games("123") == GAMES
    assert await service.enabled_mods("123") == ("kingdoms",)
    assert await service.pending_requests() == []


async def test_requesting_a_default_active_game_is_already_granted() -> None:
    """A default-active game needs no request — it is active by default."""
    service = _service()
    try:
        await service.request_access("123", ["game:aoe2"])
        raise AssertionError("expected GuildAccessError")
    except GuildAccessError:
        pass
    assert await service.enabled_games("123") == GAMES


async def test_deny_drops_request_without_grant() -> None:
    service = _service()
    await service.request_access("123", ["mod:ladder"])
    [request] = await service.pending_requests()
    await service.deny("123", request["requested_at"])
    assert await service.enabled_games("123") == GAMES
    assert await service.enabled_mods("123") == ()
    assert await service.pending_requests() == []


async def test_revoke_removes_grant() -> None:
    """An explicit list stays restrictive after one revoke.

    Revoking down to empty means NONE active (the doc is the truth,
    even empty) — the games granted along stay untouched.
    """
    service = _service()
    await service.request_access("123", ["mod:kingdoms", "mod:ladder"])
    [request] = await service.pending_requests()
    await service.approve("123", request["requested_at"])
    await service.revoke("123", "mod:ladder")
    assert await service.enabled_mods("123") == ("kingdoms",)
    await service.revoke("123", "mod:kingdoms")
    assert await service.enabled_mods("123") == ()
    assert await service.enabled_games("123") == GAMES


async def test_duplicate_request_is_refused() -> None:
    service = _service()
    await service.request_access("123", ["mod:kingdoms"])
    [request] = await service.pending_requests()
    await service.approve("123", request["requested_at"])
    try:
        await service.request_access("123", ["mod:kingdoms"])
        raise AssertionError("expected GuildAccessError")
    except GuildAccessError:
        pass


async def test_ensure_known_creates_default_doc_and_keeps_existing() -> None:
    """A guild the bot sits in gets a default doc; an existing one is untouched.

    The boot self-heal: after a database reset the panel must list the
    bot's guilds again, and a guild with explicit grants must not lose
    them when the boot re-ensures its document.
    """
    db = FakeGuildAccessDB()
    service = _service(db)
    fresh = await service.ensure_known("123")
    assert fresh["games"] == list(GAMES)  # default-active games materialized
    assert fresh["mods"] == []  # mods default inactive
    assert fresh["pending"] == {}
    assert [doc["guild_id"] for doc in await service.list_all()] == ["123"]
    await service.grant("123", ["game:aoe2"])
    kept = await service.ensure_known("123")
    assert kept["games"] == ["aoe2"]
    assert [doc["guild_id"] for doc in await service.list_all()] == ["123"]


async def test_unknown_key_is_refused() -> None:
    service = _service()
    try:
        await service.request_access("123", ["game:starcraft"])
        raise AssertionError("expected GuildAccessError")
    except GuildAccessError:
        pass
