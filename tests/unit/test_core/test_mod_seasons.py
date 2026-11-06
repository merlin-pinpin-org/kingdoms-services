"""Unit tests for the per-mod season stack seam (kingdoms-services#267)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.mod_seasons import build_mod_season_stack


class MemoryCollection:
    """In-memory Mongo-like collection."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def replace_one(self, filter: dict[str, Any], document: dict[str, Any], upsert: bool = False) -> None:
        self.docs[str(filter["_id"])] = dict(document)

    async def find_one(self, filter: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filter.items()):
                return dict(doc)
        return None

    def find(self, filter: dict[str, Any]) -> Any:
        docs = [dict(d) for d in self.docs.values() if all(d.get(k) == v for k, v in filter.items())]

        class _Cursor:
            def __init__(self, items: list[dict[str, Any]]) -> None:
                self._items = items

            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> dict[str, Any]:
                if not self._items:
                    raise StopAsyncIteration
                return self._items.pop(0)

        return _Cursor(docs)

    async def count_documents(self, filter: dict[str, Any]) -> int:
        return len([d for d in self.docs.values() if all(d.get(k) == v for k, v in filter.items())])


class MemoryInnerDatabase:
    """Mongo-like database returning per-name collections."""

    def __init__(self) -> None:
        self.collections: dict[str, MemoryCollection] = {}

    def __getitem__(self, name: str) -> MemoryCollection:
        if name not in self.collections:
            self.collections[name] = MemoryCollection()
        return self.collections[name]


class FakeSeasonDatabase:
    """SeasonDatabase-shaped adapter with an inner database."""

    def __init__(self) -> None:
        self._database = MemoryInnerDatabase()

    async def upsert_season(self, document: dict[str, Any]) -> None:
        """No-op: the season persistence is not under test here."""

    async def find_season(self, season_id: str) -> dict[str, Any] | None:
        """Never found: not under test."""
        return None

    async def find_ladder_seasons(self, ladder_id: str) -> list[dict[str, Any]]:
        """Empty: not under test."""
        return []

    async def find_active_season(self, ladder_id: str) -> dict[str, Any] | None:
        """None: not under test."""
        return None

    async def delete_season(self, season_id: str) -> None:
        """No-op: not under test."""


def test_stack_builds_the_season_scope() -> None:
    database = FakeSeasonDatabase()
    stack = build_mod_season_stack(
        mod="demo",
        guild_id="123",
        game_key="aoe2",
        database=database,
        game_data=None,
    )
    assert stack.scope == "demo:aoe2:123"
    assert stack.seasons is not None
    assert stack.enrollment is None


def test_stack_wires_enrollment_when_criteria_declared() -> None:
    database = FakeSeasonDatabase()
    stack = build_mod_season_stack(
        mod="demo",
        guild_id="123",
        game_key="aoe2",
        database=database,
        game_data=None,
        enrollment_criteria={"min_elo": 1200},
    )
    assert stack.enrollment is not None
    assert stack.enrollment.mod_name == "demo"


async def test_stack_enrollment_persists_through_the_database() -> None:
    database = FakeSeasonDatabase()
    stack = build_mod_season_stack(
        mod="demo",
        guild_id="123",
        game_key="aoe2",
        database=database,
        game_data=None,
        enrollment_criteria={"manual": True},
    )
    assert stack.enrollment is not None
    application = await stack.enrollment.apply("123", "u1", "Alice", now=10)
    assert application.state == "pending"
    collection = database._database.collections["demo:aoe2:123:applications"]
    assert "demo:123:u1" in collection.docs
    decided = await stack.enrollment.accept(application.application_id, now=20)
    assert decided.state == "accepted"
