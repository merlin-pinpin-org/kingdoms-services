"""Unit tests for the core identity import/export (kingdoms-services#138).

The identity perimeter: users.csv rows (Discord id + game profile)
become core ``profile_bindings`` — the same documents
``RegistrationService.bind_profile`` writes. Ladder collections are
never touched. Covers dedup, conflicts (profile bound to another
user), idempotence and the ``bound_at`` import date.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingdoms.core.services.identity_export import export_identity_links
from kingdoms.core.services.identity_import import import_identity_links

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

NOW_MS = 1770000000000


class FakeCollection:
    """In-memory Mongo collection with the replace_one/find_one surface."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def replace_one(self, filt: dict, doc: dict, upsert: bool = False) -> None:
        del upsert
        self.docs[doc["_id"]] = doc

    async def find_one(self, filt: dict) -> dict | None:
        key = filt.get("_id")
        if key is not None:
            return self.docs.get(key)
        for doc in self.docs.values():
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc
        return None

    def find(self, filt: dict) -> FakeCursor:
        return FakeCursor(
            [
                dict(d)
                for d in self.docs.values()
                if all(d.get(k) == v for k, v in filt.items())
            ]
        )


class FakeCursor:
    """Async iterable over a materialized list (pymongo cursor seam)."""

    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
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


@pytest.fixture()
def users_csv(tmp_path: Path) -> Path:
    """Write the association fixture CSV to the tmp dir."""
    path = tmp_path / "users.csv"
    path.write_text(USERS_CSV, encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_import_writes_core_bindings(users_csv: Path) -> None:
    """Each (user, profile) pair becomes one core binding, never a player."""
    db = FakeDatabase()
    report = await import_identity_links(db, users_csv, now_ms=NOW_MS)
    assert report.users == 3
    assert report.bindings == 4
    assert report.conflicts == 0
    bindings = db.collections["profile_bindings"].docs
    assert set(bindings) == {
        "binding:aoe2:111:101",
        "binding:aoe2:111:102",
        "binding:aoe2:222:201",
        "binding:aoe2:333:301",
    }
    assert bindings["binding:aoe2:111:101"]["bound_at"] == NOW_MS
    assert bindings["binding:aoe2:111:101"]["imported"] is True
    assert "players" not in db.collections


@pytest.mark.asyncio
async def test_import_is_idempotent(users_csv: Path) -> None:
    """Re-running with the same import date is a no-op; a new import date refreshes bound_at."""
    db = FakeDatabase()
    first = await import_identity_links(db, users_csv, now_ms=NOW_MS)
    second = await import_identity_links(db, users_csv, now_ms=NOW_MS)
    assert first.bindings == 4
    assert second.bindings == 0
    bindings = db.collections["profile_bindings"].docs
    assert bindings["binding:aoe2:111:101"]["bound_at"] == NOW_MS
    later = await import_identity_links(db, users_csv, now_ms=NOW_MS + 86_400_000)
    assert later.bindings == 4
    assert bindings["binding:aoe2:111:101"]["bound_at"] == NOW_MS + 86_400_000


@pytest.mark.asyncio
async def test_import_skips_conflicting_profiles(users_csv: Path) -> None:
    """A profile already bound to another user is skipped and counted."""
    db = FakeDatabase()
    await import_identity_links(db, users_csv, now_ms=NOW_MS)
    other = USERS_CSV.replace("222,Bravo,201", "999,Delta,201")
    path = users_csv.parent / "other.csv"
    path.write_text(other, encoding="utf-8")
    report = await import_identity_links(db, path, now_ms=NOW_MS)
    assert report.conflicts == 1
    bindings = db.collections["profile_bindings"].docs
    assert bindings["binding:aoe2:222:201"]["user_id"] == "222"


@pytest.mark.asyncio
async def test_export_round_trips_bindings(users_csv: Path, tmp_path: Path) -> None:
    """The export reads the core bindings and mirrors the import's CSV."""
    db = FakeDatabase()
    await import_identity_links(db, users_csv, now_ms=NOW_MS)
    out = tmp_path / "out.csv"
    report = await export_identity_links(db, out)
    assert report.users == 3
    assert report.rows == 4
    assert out.read_text(encoding="utf-8").splitlines() == [
        "discord_id,display_name,profile_id",
        "111,Alpha,101",
        "111,Alpha,102",
        "222,Bravo,201",
        "333,Charlie,301",
    ]
