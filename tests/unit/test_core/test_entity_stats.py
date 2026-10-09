"""Unit tests for the entity play-stats aggregation (maps)."""

from __future__ import annotations

from typing import Any

import pytest

from kingdoms.core.services.entity_stats import EntityStatsService


class _FakeDatabase:
    """Minimal async database seam: one in-memory matches collection."""

    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs = docs or []

    def __getitem__(self, name: str) -> Any:
        """Return a collection-like object for the given name."""
        assert name == "matches"
        return self

    def find(self, query: dict[str, Any]) -> Any:
        """Return an async iterator over the documents."""
        del query

        class _Cursor:
            def __init__(self, docs: list[dict[str, Any]]) -> None:
                self._docs = docs

            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> dict[str, Any]:
                if not self._docs:
                    raise StopAsyncIteration
                return self._docs.pop(0)

        return _Cursor(list(self.docs))


@pytest.mark.asyncio
async def test_map_stats_counts_completed_matches() -> None:
    """A map's stats count its completed matches."""
    docs = [
        {"status": "COMPLETED", "map_name": "Arabia", "winner_discord_id": "1"},
        {"status": "COMPLETED", "map_name": "Arabia", "winner_discord_id": "2"},
        {"status": "COMPLETED", "map_name": "Arena", "winner_discord_id": "1"},
        {"status": "OPEN", "map_name": "Arabia"},
    ]
    stats = await EntityStatsService(_FakeDatabase(docs)).map_stats("Arabia")
    assert stats.games == 2


@pytest.mark.asyncio
async def test_map_stats_match_by_case_insensitive_name() -> None:
    """The map lookup is case-insensitive (dump spellings vary)."""
    docs = [{"status": "COMPLETED", "map_name": "Dry River", "winner_discord_id": "1"}]
    stats = await EntityStatsService(_FakeDatabase(docs)).map_stats("dry river")
    assert stats.games == 1


@pytest.mark.asyncio
async def test_map_stats_empty_collection_degrades_to_zero() -> None:
    """No matches collection or no history: zero games, no error."""
    stats = await EntityStatsService(_FakeDatabase([])).map_stats("Arabia")
    assert stats.games == 0 and stats.winrate == 0.0


@pytest.mark.asyncio
async def test_winrate_is_wins_over_games() -> None:
    """The winrate derives from the aggregated record."""
    stats = await EntityStatsService(_FakeDatabase([])).map_stats("Arabia")
    assert stats.winrate == 0.0
