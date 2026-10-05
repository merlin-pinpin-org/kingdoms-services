"""Unit tests for the legacy match-history export (kingdoms-services#138).

Symmetric counterpart of the import tests: legacy matches with their
rating-history entries become one minimal CSV row per match — exactly
the columns the import reads. The export re-imports cleanly (round-trip).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.mods.ladder.legacy_export import export_legacy
from kingdoms.mods.ladder.legacy_import import import_legacy, load_matches


class FakeCollection:
    """In-memory Mongo collection with the find/find_one/replace_one surface."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def find(self, filt: dict) -> "_AsyncCursor":
        del filt
        return _AsyncCursor(list(self.docs.values()))

    async def find_one(self, filt: dict) -> dict | None:
        key = filt.get("_id")
        return self.docs.get(key) if key is not None else None

    async def replace_one(self, filt: dict, doc: dict, upsert: bool = False) -> None:
        del upsert
        self.docs[doc["_id"]] = doc


class _AsyncCursor:
    """Async-iterable cursor over a snapshot of documents."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self) -> "_AsyncCursor":
        return self

    async def __anext__(self) -> dict:
        if not self._docs:
            raise StopAsyncIteration
        return self._docs.pop(0)


class FakeDatabase:
    """In-memory database keyed by collection name."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def _match(match_id: str, host: str, guest: str, winner: str) -> dict:
    return {
        "_id": f"legacy:{match_id}",
        "ladder_id": "lad-1",
        "status": "COMPLETED",
        "created_at": 1790263410,
        "host": {"user_id": host},
        "guest": {"user_id": guest},
        "map_snapshot": {"name": "Frigid Lake"},
        "winner_user_id": winner,
        "loser_user_id": guest if winner == host else host,
        "completed_at": 1790263410,
    }


def _history(match_id: str, user_id: str, before: int, delta: int) -> dict:
    return {
        "_id": f"rh:legacy:{match_id}:{user_id}",
        "ladder_id": "lad-1",
        "match_id": f"legacy:{match_id}",
        "user_id": user_id,
        "rating_before": float(before),
        "rating_after": float(before + delta),
        "delta": float(delta),
        "k_used": 0.0,
        "reason": "MATCH_RESULT",
        "created_at": 1790263410,
    }


@pytest.fixture()
def populated_db() -> FakeDatabase:
    db = FakeDatabase()
    players = db.collections.setdefault("players", FakeCollection())
    players.docs["player:lad-1:111"] = {
        "_id": "player:lad-1:111",
        "ladder_id": "lad-1",
        "user_id": "111",
        "display_name": "Alpha",
        "legacy_linked_profiles": ["101"],
    }
    players.docs["player:lad-1:222"] = {
        "_id": "player:lad-1:222",
        "ladder_id": "lad-1",
        "user_id": "222",
        "display_name": "Bravo",
    }
    matches = db.collections.setdefault("matches", FakeCollection())
    matches.docs["legacy:245"] = _match("245", "111", "222", "111")
    history = db.collections.setdefault("rating_history", FakeCollection())
    history.docs["rh:legacy:245:111"] = _history("245", "111", 1116, 17)
    history.docs["rh:legacy:245:222"] = _history("245", "222", 1063, -17)
    return db


@pytest.mark.asyncio
async def test_export_writes_one_minimal_row_per_match(
    populated_db: FakeDatabase, tmp_path: Path
) -> None:
    out = tmp_path / "matches.csv"
    report = await export_legacy(populated_db, "lad-1", out)
    assert report.matches == 1
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("ladder_match_id,status,match_id,map_name")
    row = lines[1].split(",")
    assert row[0] == "245"
    assert row[4] == "1790263410"
    assert row[5] == "Alpha"
    assert row[6] == "111"
    assert row[8] == "1116"
    assert row[9] == "17"
    assert row[10] == "Bravo"
    assert row[15] == "111"


@pytest.mark.asyncio
async def test_export_reimports_symmetrically(
    populated_db: FakeDatabase, tmp_path: Path
) -> None:
    out = tmp_path / "matches.csv"
    await export_legacy(populated_db, "lad-1", out)
    matches = load_matches(out)
    assert len(matches) == 1
    assert matches[0].ladder_match_id == "245"
    assert matches[0].winner_discord_id == "111"

    target = FakeDatabase()
    report = await import_legacy(target, "lad-1", out)
    assert report.matches == 1
    bravo = target.collections["players"].docs["player:lad-1:222"]
    assert bravo["rating"] == 1063 - 17
