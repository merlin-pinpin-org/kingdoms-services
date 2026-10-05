"""Unit tests for the profile-links export (kingdoms-services#138).

Symmetric counterpart of the import tests: players with linked profiles
become one CSV row per (player, profile); players without links are
skipped. The export re-imports cleanly (round-trip).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.mods.ladder.profile_links_export import export_profile_links
from kingdoms.mods.ladder.profile_links_import import import_profile_links


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


def _player(user_id: str, name: str, linked: list[str]) -> dict:
    return {
        "_id": f"player:lad-1:{user_id}",
        "ladder_id": "lad-1",
        "user_id": user_id,
        "display_name": name,
        "rating": 1000,
        "legacy_linked_profiles": linked,
    }


@pytest.fixture()
def populated_db() -> FakeDatabase:
    db = FakeDatabase()
    players = db.collections.setdefault("players", FakeCollection())
    players.docs[_player("111", "Alpha", ["101", "102"])["_id"]] = _player(
        "111", "Alpha", ["101", "102"]
    )
    players.docs[_player("222", "Bravo", ["201"])["_id"]] = _player("222", "Bravo", ["201"])
    players.docs[_player("333", "Charlie", [])["_id"]] = _player("333", "Charlie", [])
    return db


@pytest.mark.asyncio
async def test_links_export_writes_one_row_per_profile(
    populated_db: FakeDatabase, tmp_path: Path
) -> None:
    out = tmp_path / "users.csv"
    report = await export_profile_links(populated_db, "lad-1", out)
    assert report.players == 2
    assert report.rows == 3
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0] == "discord_id,display_name,profile_id"
    assert lines[1] == "111,Alpha,101"
    assert lines[2] == "111,Alpha,102"
    assert lines[3] == "222,Bravo,201"


@pytest.mark.asyncio
async def test_links_export_reimports_symmetrically(
    populated_db: FakeDatabase, tmp_path: Path
) -> None:
    out = tmp_path / "users.csv"
    await export_profile_links(populated_db, "lad-1", out)
    target = FakeDatabase()
    report = await import_profile_links(target, "lad-1", out)
    assert report.players == 2
    assert report.linked_profiles == 3
    alpha = target.collections["players"].docs["player:lad-1:111"]
    assert alpha["legacy_linked_profiles"] == ["101", "102"]
    assert alpha["display_name"] == "Alpha"
