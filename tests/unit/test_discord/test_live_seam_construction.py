"""The live seam's CONSTRUCTION — the wiring, not just the service.

The 2026-11-06 crash: the service passed its test (a correct adapter)
but the SEAM built it over the RAW database — 'AsyncCollection object
is not callable' in the live panel. A service test cannot catch a
wiring error; only a test of the CONSTRUCTION can. This is that test.
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeRawDatabase:
    """The shape live.py receives: the raw async database (collections)."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def __getitem__(self, name: str) -> Any:
        assert name == "profile_bindings", f"unexpected collection {name}"

        class _Col:
            def find(inner_self, query: dict[str, Any]) -> Any:  # noqa: N805
                class _Cursor:
                    def __init__(self, docs: list[dict[str, Any]]) -> None:
                        self._docs = docs

                    def __aiter__(self) -> Any:
                        return self

                    async def __anext__(self) -> dict[str, Any]:
                        if not self._docs:
                            raise StopAsyncIteration
                        return self._docs.pop(0)

                wanted = set(query.get("profile_id", {}).get("$in", []))
                return _Cursor([d for d in self.docs.values() if d["profile_id"] in wanted])

        col = _Col()
        col.docs = self.docs
        return col


@pytest.mark.asyncio
async def test_the_seam_builds_the_service_over_the_adapter() -> None:
    """The seam's service answers through the REAL construction path.

    If the seam ever passes the raw database again (the 2026-11-06
    crash), this test fails with the same TypeError the panel saw.
    """
    from kingdoms.discord.live import _registration_bindings_service

    raw = _FakeRawDatabase()
    raw.docs = {
        "b1": {"profile_id": "111", "user_id": "u1", "game_key": "aoe2"},
        "b2": {"profile_id": "222", "user_id": "u2", "game_key": "aoe2"},
    }
    service = _registration_bindings_service(raw)
    assert service is not None, "the seam must build the service"
    bindings = await service.find_bindings_by_profiles("aoe2", ["222", "111"])
    assert sorted(b["profile_id"] for b in bindings) == ["111", "222"]
