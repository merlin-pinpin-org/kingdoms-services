"""The batched bindings read — the panels' hot path (rule 5).

The 2026-11-06 live test caught the method living in the Protocol
instead of the SERVICE: the players panel crashed with
'RegistrationService has no attribute find_bindings_by_profiles'.
The method had NO test — this IS the test, and it exercises the
SERVICE (the class the panels call), not the port.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeDatabase:
    """The port's adapter, recording the batched read."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def find_bindings_by_profiles(
        self, game_key: str, profile_ids: list[str]
    ) -> list[dict[str, Any]]:
        self.calls.append({"game_key": game_key, "profile_ids": sorted(profile_ids)})
        return [
            {"profile_id": pid, "user_id": f"user-{pid}", "game_key": game_key}
            for pid in sorted(profile_ids)
        ]


@pytest.mark.asyncio
async def test_the_service_reads_the_bindings_in_batch() -> None:
    """The SERVICE exposes the batched read (the panels' seam)."""
    from kingdoms.core.services.registration import RegistrationService

    database = _FakeDatabase()
    service = RegistrationService(database, {}, None, None)  # type: ignore[arg-type]
    bindings = await service.find_bindings_by_profiles("aoe2", ["222", "111"])

    assert [b["profile_id"] for b in bindings] == ["111", "222"]
    assert database.calls == [{"game_key": "aoe2", "profile_ids": ["111", "222"]}]


@pytest.mark.asyncio
async def test_the_batched_read_degrades_on_empty() -> None:
    """An empty batch answers empty — no database round-trip."""
    from kingdoms.core.services.registration import RegistrationService

    database = _FakeDatabase()
    service = RegistrationService(database, {}, None, None)  # type: ignore[arg-type]
    assert await service.find_bindings_by_profiles("aoe2", []) == []
    assert database.calls == []
