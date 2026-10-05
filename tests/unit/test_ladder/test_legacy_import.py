"""Unit tests for the legacy match-history import (kingdoms-services#138).

The ladder perimeter: matches.csv rows deduplicate by ladder_match_id,
CANCELED is skipped, elo replays into rating_history, players keep their
last replayed rating, and ghost players (absent from any association
dump, e.g. unlinked profiles) import from the match rows alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.mods.ladder.legacy_import import import_legacy, load_matches

GHOST_DISCORD_ID = "555"

_MATCH_HEADER = (
    "ladder_match_id,status,match_id,map_name,game_started_at,game_completed_at,"
    "game_duration,host_name,host_discord_id,host_profile_id,host_elo_before,"
    "host_elo_diff,guest_name,guest_discord_id,guest_profile_id,guest_elo_before,"
    "guest_elo_diff,host_faction_key,guest_faction_key,winner_name,"
    "winner_discord_id,loser_name,loser_discord_id"
)

MATCHES_CSV = "\n".join(
    [
        _MATCH_HEADER,
        "12,COMPLETED,9002,Arabia,100,110,60,Alpha,111,101,1000,20,"
        "Bravo,222,201,1000,-20,franks,britons,Alpha,111,Bravo,222",
        "12,COMPLETED,9002,Arabia,100,110,60,Alpha,111,102,1000,20,"
        "Bravo,222,201,1000,-20,franks,britons,Alpha,111,Bravo,222",
        "11,CANCELED,,Hideout,,,,,Alpha,111,101,,,Bravo,222,201,,,,,,,,",
        "10,COMPLETED,9001,Arabia,50,160,60,Bravo,222,201,1020,-15,"
        "Charlie,333,301,1000,15,huns,incas,Charlie,333,Bravo,222",
        "9,COMPLETED,9000,Hideout,10,70,60,Ghost,555,501,1000,12,"
        "Delta,444,401,1000,-12,goths,mayans,Ghost,555,Delta,444",
        "9,COMPLETED,9000,Hideout,10,70,60,Ghost,555,502,1000,12,"
        "Delta,444,401,1000,-12,goths,mayans,Ghost,555,Delta,444",
        "8,CANCELED,,Arabia,,,,,Ghost,555,501,,,Bravo,222,201,,,,,,,,",
        "",
    ]
)


class FakeCollection:
    """In-memory Mongo collection with the replace_one/find_one surface."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def replace_one(self, filt: dict, doc: dict, upsert: bool = False) -> None:
        del upsert, filt
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
def matches_csv(tmp_path: Path) -> Path:
    """Write the match-history fixture CSV to the tmp dir."""
    path = tmp_path / "matches.csv"
    path.write_text(MATCHES_CSV, encoding="utf-8")
    return path


def test_load_matches_deduplicates_cartesian_rows(matches_csv: Path) -> None:
    """The cartesian duplicate collapses; CANCELED is skipped."""
    matches = load_matches(matches_csv)
    assert [m.ladder_match_id for m in matches] == ["9", "12", "10"]
    assert all(m.winner_discord_id for m in matches)


@pytest.mark.asyncio
async def test_import_creates_players_matches_and_history(matches_csv: Path) -> None:
    """Players, matches and rating_history all land in the database."""
    db = FakeDatabase()
    report = await import_legacy(db, "lad-1", matches_csv)
    assert report.players == 5
    assert report.matches == 3
    assert report.rating_history_entries == 6

    players = db.collections["players"].docs
    matches = db.collections["matches"].docs
    history = db.collections["rating_history"].docs

    assert set(players) == {
        "player:lad-1:111",
        "player:lad-1:222",
        "player:lad-1:333",
        "player:lad-1:444",
        f"player:lad-1:{GHOST_DISCORD_ID}",
    }
    assert set(matches) == {"legacy:10", "legacy:12", "legacy:9"}
    assert set(history) == {
        "rh:legacy:10:222",
        "rh:legacy:10:333",
        "rh:legacy:12:111",
        "rh:legacy:12:222",
        "rh:legacy:9:444",
        f"rh:legacy:9:{GHOST_DISCORD_ID}",
    }


@pytest.mark.asyncio
async def test_final_ratings_replay_the_recorded_elo(matches_csv: Path) -> None:
    """The final rating is the last replayed elo (Alpha 1020, Bravo 1005...)."""
    db = FakeDatabase()
    await import_legacy(db, "lad-1", matches_csv)
    players = db.collections["players"].docs
    assert players["player:lad-1:111"]["rating"] == 1020
    assert players["player:lad-1:111"]["wins"] == 1
    assert players["player:lad-1:222"]["rating"] == 1005
    assert players["player:lad-1:222"]["wins"] == 0
    assert players["player:lad-1:222"]["losses"] == 2
    assert players["player:lad-1:333"]["rating"] == 1015
    assert players["player:lad-1:333"]["wins"] == 1


@pytest.mark.asyncio
async def test_ghost_player_is_imported_from_match_rows(matches_csv: Path) -> None:
    """A player absent from any association dump is still imported.

    His display name comes from the match rows, his rating from the
    replay. The match-import does not write profile rosters — the
    association import (profile_links_import) owns them.
    """
    db = FakeDatabase()
    await import_legacy(db, "lad-1", matches_csv)
    ghost = db.collections["players"].docs[f"player:lad-1:{GHOST_DISCORD_ID}"]
    assert ghost["display_name"] == "Ghost"
    assert ghost["rating"] == 1012
    assert ghost["wins"] == 1


@pytest.mark.asyncio
async def test_import_is_idempotent(matches_csv: Path) -> None:
    """Re-running the import over the same data changes nothing."""
    db = FakeDatabase()
    first = await import_legacy(db, "lad-1", matches_csv)
    snapshot = {name: dict(col.docs) for name, col in db.collections.items()}
    second = await import_legacy(db, "lad-1", matches_csv)
    assert second == first
    assert {name: dict(col.docs) for name, col in db.collections.items()} == snapshot


@pytest.mark.asyncio
async def test_history_entries_carry_the_recorded_deltas(matches_csv: Path) -> None:
    """Each rating_history line replays before/after/delta as exported."""
    db = FakeDatabase()
    await import_legacy(db, "lad-1", matches_csv)
    history = db.collections["rating_history"].docs
    alpha = history["rh:legacy:12:111"]
    assert alpha["rating_before"] == 1000.0
    assert alpha["rating_after"] == 1020.0
    assert alpha["delta"] == 20.0
    assert alpha["reason"] == "MATCH_RESULT"
    bravo_late = history["rh:legacy:10:222"]
    assert bravo_late["rating_before"] == 1020.0
    assert bravo_late["rating_after"] == 1005.0
    assert history["rh:legacy:12:222"]["rating_before"] == 1000.0
