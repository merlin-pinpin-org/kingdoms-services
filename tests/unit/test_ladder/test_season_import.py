"""Unit tests for the season import orchestrator (kingdoms-services#205).

Verifies the full load: seed (maps/pools/ladder/season), rotation
replay with a guard against re-runs, season activation, then
association and match imports — and that the whole orchestration is
idempotent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kingdoms.mods.ladder.season_import import import_season

USERS_CSV = """discord_id,display_name,profile_id
111,Alpha,101
222,Bravo,201
"""

MATCHES_HEADER = (
    "ladder_match_id,status,match_id,map_name,game_completed_at,"
    "host_name,host_discord_id,host_profile_id,host_elo_before,host_elo_diff,"
    "guest_name,guest_discord_id,guest_profile_id,guest_elo_before,"
    "guest_elo_diff,winner_discord_id"
)

MATCHES_CSV = "\n".join(
    [
        MATCHES_HEADER,
        "2,COMPLETED,9002,Kawasan,200,Alpha,111,101,1020,-10,Bravo,222,201,1000,10,222",
        "1,COMPLETED,9001,Arabia,100,Alpha,111,101,1000,20,Bravo,222,201,1000,-20,111",
        "",
    ]
)

SEASON_YAML = """game_key: aoe2
maps:
  - name: Arabia
    filename: "arabia"
    description: "Classic"
  - name: Kawasan
    filename: "kawasan"
    description: "Hybrid"
map_pools:
  - name: "Rotation 1"
    description: "Opening"
    maps: [Arabia]
  - name: "Rotation 2"
    description: "Closing"
    maps: [Kawasan]
ladders:
  - owner_ref: "guild:default"
    name: "AoE2 Community Ladder"
    map_pool: "Rotation 1"
    season:
      name: "Season 1"
      start_in_days: 0
      duration_days: 180
      reset_ratings: false
"""


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs
        self._iter = iter(docs)

    def sort(self, key: str, direction: int) -> _FakeCursor:
        return _FakeCursor(sorted(self._docs, key=lambda d: d.get(key, 0), reverse=direction < 0))

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._docs

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    async def replace_one(self, filt: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del upsert, filt
        self.docs[doc["_id"]] = doc

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        key = filt.get("_id")
        if key is not None:
            return self.docs.get(key)
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc
        return None

    def find(self, filt: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self.docs.values() if all(d.get(k) == v for k, v in filt.items())])


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


async def test_import_season_seeds_rotates_and_imports(tmp_path: Path) -> None:
    db = FakeDatabase()
    report = await import_season(
        db,
        _write(tmp_path, "season.yaml", SEASON_YAML),
        _write(tmp_path, "users.csv", USERS_CSV),
        _write(tmp_path, "matches.csv", MATCHES_CSV),
    )
    assert report.seed["maps"] == 2
    assert report.seed["map_pools"] == 2
    assert report.seed["ladders"] == 1
    assert report.seed["seasons"] == 1
    assert report.matches == 2
    assert report.players == 2
    assert report.linked_profiles == 2
    assert report.rotations == 1

    ladders = db.collections["ladders"].docs
    ladder = ladders["ladder:aoe2:guild:default"]
    assert ladder["active_map_pool_id"] == "map_pool:aoe2:Rotation 2"

    seasons = db.collections["seasons"].docs
    season = seasons["season:ladder:aoe2:guild:default:Season 1"]
    assert season["state"] == "active"

    activations = db.collections["map_pool_history"].docs
    assert len(activations) == 2

    assert "player:ladder:aoe2:guild:default:111" in db.collections["players"].docs
    assert "legacy:1" in db.collections["matches"].docs


async def test_import_season_is_idempotent(tmp_path: Path) -> None:
    db = FakeDatabase()
    paths = (
        _write(tmp_path, "season.yaml", SEASON_YAML),
        _write(tmp_path, "users.csv", USERS_CSV),
        _write(tmp_path, "matches.csv", MATCHES_CSV),
    )
    await import_season(db, *paths)
    second = await import_season(db, *paths)
    assert second.seed["maps"] == 0
    assert second.seed["map_pools"] == 0
    assert second.seed["ladders"] == 0
    assert second.seed["seasons"] == 0
    assert second.rotations == 0
    assert len(db.collections["map_pool_history"].docs) == 2
    assert len(db.collections["matches"].docs) == 2
