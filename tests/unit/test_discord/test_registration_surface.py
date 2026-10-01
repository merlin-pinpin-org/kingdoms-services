"""Unit tests for the /register Discord surface and platform adapters (#133)."""

from __future__ import annotations

from typing import Any

from kingdoms.discord.registration import build_enrollment_screen, render_profile_error


class FakeDatabase:
    """In-memory stand-in for the async MongoDB adapter."""

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)


class _FakeCollection:
    def __init__(self, db: FakeDatabase, name: str) -> None:
        self._db = db
        self._name = name

    def _docs(self) -> dict[str, dict[str, Any]]:
        return self._db.collections.setdefault(self._name, {})

    async def replace_one(self, filt: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del filt, upsert
        self._docs()[doc["_id"]] = doc

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self._docs().values():
            if all(doc.get(k) == v for k, v in filt.items()):
                return doc
        return None

    def find(self, filt: dict[str, Any]) -> _FakeCursor:
        return _FakeCursor([d for d in self._docs().values() if all(d.get(k) == v for k, v in filt.items())])


class _FakeCursor:
    """Async-iterable stand-in for a MongoDB cursor."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self) -> _FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as err:
            raise StopAsyncIteration from err


async def test_mongo_registration_database_bindings() -> None:
    from kingdoms.discord.registration_platform import MongoRegistrationDatabase

    db = FakeDatabase()
    adapter = MongoRegistrationDatabase(db)
    await adapter.upsert_entry(
        "profile_bindings",
        {"_id": "binding:aoe2:10", "user_id": "10", "game_key": "aoe2", "profile_id": "A1"},
    )
    assert await adapter.find_entry("profile_bindings", "binding:aoe2:10") is not None
    assert await adapter.find_binding_by_profile("aoe2", "A1") is not None
    assert await adapter.find_binding_by_profile("aoe2", "ZZZ") is None
    bindings = await adapter.find_user_bindings("10")
    assert len(bindings) == 1
    game_bindings = await adapter.list_bindings_for_game("aoe2")
    assert len(game_bindings) == 1


async def test_aoe2_seam_rejects_without_provider() -> None:
    from kingdoms.discord.registration_platform import Aoe2ProfileValidationSeam

    seam = Aoe2ProfileValidationSeam(provider_uri="")
    assert await seam.validate_profile("12345") is None


def test_error_messages_cover_workflow_errors() -> None:
    for error in ("empty_profile_id", "invalid_profile", "profile_taken", "unknown"):
        message = render_profile_error(error)
        assert isinstance(message, str)
        assert len(message) > 0


def test_enrollment_screen_mentions_user() -> None:
    text = build_enrollment_screen({"user_id": "42"})
    assert "<@42>" in text
