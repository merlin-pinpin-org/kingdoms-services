"""Unit tests for the JeanJack import pipeline (kingdoms-services#138).

Covers the acceptance properties: imported players can join the ladder,
imported history appears in rating_history, the leaderboard reflects it.
The fixtures reproduce the real export's quirks: cartesian-product
duplicate rows (one per side-profile pair), CANCELED matches, a player
without any match, and multiple profile links per user.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.mods.ladder.jeanjack_import import import_jeanjack, load_matches, load_users

USERS_CSV = "\n".join(
    [
        "discord_id,ladder_name,profile_id,profile_created_at",
        "111,Alpha,101,2026-02-07 16:10:00",
        "111,Alpha,102,2026-02-07 16:10:00",
        "222,Bravo,201,2026-02-07 16:10:00",
        "333,Charlie,301,2026-02-07 16:10:00",
        "444,Delta,401,2026-04-10 21:55:23",
        "",
    ]
)

# 555 (Ghost) played with profiles 501/502 he later unlinked: he appears in
# matches but not in the users export (the Aubin case).
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
def csv_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write the fixture CSVs to the tmp dir."""
    users = tmp_path / "users.csv"
    users.write_text(USERS_CSV, encoding="utf-8")
    matches = tmp_path / "matches.csv"
    matches.write_text(MATCHES_CSV, encoding="utf-8")
    return users, matches


def test_load_users_keeps_one_row_per_link(csv_files: tuple[Path, Path]) -> None:
    """Users load one row per profile link; the import dedups by discord_id."""
    users = load_users(csv_files[0])
    assert [u.discord_id for u in users] == ["111", "111", "222", "333", "444"]


def test_load_matches_deduplicates_cartesian_rows(csv_files: tuple[Path, Path]) -> None:
    """The cartesian duplicate collapses; CANCELED is skipped."""
    matches = load_matches(csv_files[1])
    assert [m.ladder_match_id for m in matches] == ["9", "12", "10"]
    assert all(m.winner_discord_id for m in matches)


@pytest.mark.asyncio
async def test_import_creates_players_matches_and_history(csv_files: tuple[Path, Path]) -> None:
    """Players, matches and rating_history all land in the database."""
    db = FakeDatabase()
    report = await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    assert report.players == 5
    assert report.matches == 3
    assert report.rating_history_entries == 6
    assert report.detached_profiles == 2

    players = db.collections["players"].docs
    matches = db.collections["matches"].docs
    history = db.collections["rating_history"].docs

    assert set(players) == {
        "player:lad-1:111",
        "player:lad-1:222",
        "player:lad-1:333",
        "player:lad-1:444",
        "player:lad-1:555",
    }
    assert set(matches) == {"jj:10", "jj:12", "jj:9"}
    assert set(history) == {
        "rh:jj:10:222",
        "rh:jj:10:333",
        "rh:jj:12:111",
        "rh:jj:12:222",
        "rh:jj:9:444",
        "rh:jj:9:555",
    }


@pytest.mark.asyncio
async def test_final_ratings_replay_the_recorded_elo(csv_files: tuple[Path, Path]) -> None:
    """The final rating is the last replayed elo (Alpha 1020, Bravo 1005...)."""
    db = FakeDatabase()
    await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    players = db.collections["players"].docs
    assert players["player:lad-1:111"]["rating"] == 1020
    assert players["player:lad-1:111"]["wins"] == 1
    assert players["player:lad-1:222"]["rating"] == 1005
    assert players["player:lad-1:222"]["wins"] == 0
    assert players["player:lad-1:222"]["losses"] == 2
    assert players["player:lad-1:333"]["rating"] == 1015
    assert players["player:lad-1:333"]["wins"] == 1


@pytest.mark.asyncio
async def test_ghost_player_is_imported_from_match_rows(csv_files: tuple[Path, Path]) -> None:
    """A player absent from the users export is still imported (Aubin).

    His display name comes from the match rows, his rating from the
    replay, and his detached profiles are recorded but never linked.
    """
    db = FakeDatabase()
    await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    ghost = db.collections["players"].docs[f"player:lad-1:{GHOST_DISCORD_ID}"]
    assert ghost["display_name"] == "Ghost"
    assert ghost["rating"] == 1012
    assert ghost["wins"] == 1
    assert ghost["jeanjack_linked_profiles"] == []
    assert sorted(ghost["jeanjack_detached_profiles"]) == ["501", "502"]


@pytest.mark.asyncio
async def test_linked_players_keep_their_profiles(csv_files: tuple[Path, Path]) -> None:
    """Linked profiles are recorded as active; Delta's one match shows."""
    db = FakeDatabase()
    await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    alpha = db.collections["players"].docs["player:lad-1:111"]
    assert sorted(alpha["jeanjack_linked_profiles"]) == ["101", "102"]
    assert alpha["jeanjack_detached_profiles"] == []
    delta = db.collections["players"].docs["player:lad-1:444"]
    assert delta["rating"] == 988
    assert delta["losses"] == 1
    assert delta["jeanjack_linked_profiles"] == ["401"]


@pytest.mark.asyncio
async def test_import_is_idempotent(csv_files: tuple[Path, Path]) -> None:
    """Re-running the import over the same data changes nothing."""
    db = FakeDatabase()
    first = await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    snapshot = {name: dict(col.docs) for name, col in db.collections.items()}
    second = await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    assert second == first
    assert {name: dict(col.docs) for name, col in db.collections.items()} == snapshot


@pytest.mark.asyncio
async def test_history_entries_carry_the_recorded_deltas(csv_files: tuple[Path, Path]) -> None:
    """Each rating_history line replays before/after/delta as exported."""
    db = FakeDatabase()
    await import_jeanjack(db, "lad-1", csv_files[0], csv_files[1])
    history = db.collections["rating_history"].docs
    alpha = history["rh:jj:12:111"]
    assert alpha["rating_before"] == 1000.0
    assert alpha["rating_after"] == 1020.0
    assert alpha["delta"] == 20.0
    assert alpha["reason"] == "MATCH_RESULT"
    bravo_late = history["rh:jj:10:222"]
    assert bravo_late["rating_before"] == 1020.0
    assert bravo_late["rating_after"] == 1005.0
    assert history["rh:jj:12:222"]["rating_before"] == 1000.0
