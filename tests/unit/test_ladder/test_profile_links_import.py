"""Unit tests for the profile-links import (kingdoms-services#138).

The association perimeter: users.csv rows (Discord id + AoE2 profile
link) become ``legacy_linked_profiles`` rosters on the ladder players.
Covers dedup of duplicate rows, creation of never-played players,
enrichment (never overwrite) of existing player docs, and idempotence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.mods.ladder.profile_links_import import import_profile_links

USERS_CSV = "\n".join(
    [
        "discord_id,display_name,profile_id",
        "111,Alpha,101",
        "111,Alpha,102",
        "111,Alpha,101",
        "222,Bravo,201",
        "333,Charlie,301",
        "",
    ]
)


class FakeCollection:
    """In-memory Mongo collection with the replace_one/find_one surface."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def replace_one(self, filt: dict, doc: dict, upsert: bool = False) -> None:
        del upsert
        self.docs[doc["_id"]] = doc

    async def find_one(self, filt: dict) -> dict | None:
        key = filt.get("_id")
        return self.docs.get(key) if key is not None else None


class FakeDatabase:
    """In-memory database keyed by collection name."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


@pytest.fixture()
def users_csv(tmp_path: Path) -> Path:
    """Write the association fixture CSV to the tmp dir."""
    path = tmp_path / "users.csv"
    path.write_text(USERS_CSV, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_links_import_creates_players_with_rosters(users_csv: Path) -> None:
    """Each distinct Discord id gets a player with dedup linked profiles."""
    db = FakeDatabase()
    report = await import_profile_links(db, "lad-1", users_csv)
    assert report.players == 3
    assert report.linked_profiles == 4
    players = db.collections["players"].docs
    alpha = players["player:lad-1:111"]
    assert alpha["legacy_linked_profiles"] == ["101", "102"]
    assert alpha["display_name"] == "Alpha"
    assert alpha["rating"] == 1000
    bravo = players["player:lad-1:222"]
    assert bravo["legacy_linked_profiles"] == ["201"]


@pytest.mark.asyncio
async def test_links_import_enriches_existing_players(users_csv: Path) -> None:
    """A player doc with stats keeps them; only the roster is written."""
    db = FakeDatabase()
    existing = {
        "_id": "player:lad-1:222",
        "guild_id": "g",
        "ladder_id": "lad-1",
        "user_id": "222",
        "platform": "discord",
        "display_name": "Bravo",
        "rating": 1042,
        "rating_max": 1042,
        "matches_count": 5,
        "wins": 3,
        "losses": 2,
        "streak": 1,
        "registered_at": 123,
    }
    db.collections.setdefault("players", FakeCollection()).docs[existing["_id"]] = existing
    await import_profile_links(db, "lad-1", users_csv)
    bravo = db.collections["players"].docs["player:lad-1:222"]
    assert bravo["rating"] == 1042
    assert bravo["wins"] == 3
    assert bravo["legacy_linked_profiles"] == ["201"]
    assert bravo["legacy_detached_profiles"] == []


@pytest.mark.asyncio
async def test_links_import_is_idempotent(users_csv: Path) -> None:
    """Re-running the import over the same CSV changes nothing."""
    db = FakeDatabase()
    first = await import_profile_links(db, "lad-1", users_csv)
    snapshot = {name: dict(col.docs) for name, col in db.collections.items()}
    second = await import_profile_links(db, "lad-1", users_csv)
    assert second == first
    assert {name: dict(col.docs) for name, col in db.collections.items()} == snapshot
