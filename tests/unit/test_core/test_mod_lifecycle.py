"""Mod lifecycle: the per-guild active/suspended state (kingdoms-services#267)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.mod_lifecycle import (
    STATE_ACTIVE,
    STATE_SUSPENDED,
    ModLifecycleService,
    ModSuspendedError,
)


class MemoryStates:
    """In-memory ModLifecycleDatabase implementation."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def upsert_mod_state(self, document: dict[str, Any]) -> None:
        self.docs[str(document["_id"])] = dict(document)

    async def find_mod_state(self, guild_id: str, mod: str) -> dict[str, Any] | None:
        return self.docs.get(f"{guild_id}:{mod}")


async def test_default_state_is_active() -> None:
    """A mod never suspended is active — the flag is opt-in, never blocking."""
    service = ModLifecycleService(MemoryStates())
    assert await service.state("g1", "demo") == STATE_ACTIVE
    assert await service.is_suspended("g1", "demo") is False
    await service.require_usable("g1", "demo")  # no raise


async def test_suspend_then_require_refuses() -> None:
    """A suspended mod refuses the players' flows with the recorded reason."""
    db = MemoryStates()
    service = ModLifecycleService(db)
    await service.suspend("g1", "demo", now=100, reason="maintenance")
    assert await service.is_suspended("g1", "demo") is True
    try:
        await service.require_usable("g1", "demo")
        raise AssertionError("require_usable must raise on a suspended mod")
    except ModSuspendedError as error:
        assert error.mod == "demo"
        assert error.guild_id == "g1"
        assert "maintenance" in str(error)
    # the state is per guild — another guild stays active
    assert await service.is_suspended("g2", "demo") is False


async def test_resume_restores_the_players_flows() -> None:
    """Resume clears the suspension (idempotent)."""
    db = MemoryStates()
    service = ModLifecycleService(db)
    await service.suspend("g1", "demo", now=100, reason="pause")
    await service.resume("g1", "demo", now=200)
    assert await service.state("g1", "demo") == STATE_ACTIVE
    await service.require_usable("g1", "demo")  # no raise
    await service.resume("g1", "demo", now=300)  # idempotent
    assert await service.is_suspended("g1", "demo") is False


async def test_suspend_is_idempotent_and_refreshes_the_reason() -> None:
    """Re-suspending refreshes the reason without duplicating state."""
    db = MemoryStates()
    service = ModLifecycleService(db)
    await service.suspend("g1", "demo", now=100, reason="first")
    await service.suspend("g1", "demo", now=200, reason="second")
    assert len(db.docs) == 1
    assert db.docs["g1:demo"]["reason"] == "second"
    assert db.docs["g1:demo"]["state"] == STATE_SUSPENDED
