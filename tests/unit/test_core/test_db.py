"""Unit tests for the MongoDB client setup (kingdoms-services#4).

These tests only exercise client construction and singleton lifecycle: no
MongoDB server is required (clients connect lazily). Live-database round
trips are exercised by CI jobs that boot MongoDB via docker compose.
"""

from __future__ import annotations

import pytest
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.synchronous.mongo_client import MongoClient

from kingdoms.core.models import db


@pytest.fixture(autouse=True)
async def _reset_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost:27017")
    monkeypatch.setenv("MONGO_DB", "kingdoms_test")
    db.close_sync_client()
    await db.close_async_client()
    yield
    db.close_sync_client()
    await db.close_async_client()


def test_get_sync_client_is_singleton() -> None:
    client = db.get_sync_client()
    assert isinstance(client, MongoClient)
    assert client is db.get_sync_client()


async def test_get_async_client_is_singleton() -> None:
    client = db.get_async_client()
    assert isinstance(client, AsyncMongoClient)
    assert client is db.get_async_client()


def test_close_sync_client_releases_singleton() -> None:
    first = db.get_sync_client()
    db.close_sync_client()
    assert db.get_sync_client() is not first


async def test_close_async_client_releases_singleton() -> None:
    first = db.get_async_client()
    await db.close_async_client()
    assert db.get_async_client() is not first


def test_get_database_uses_env_db_name() -> None:
    assert db.get_database().name == "kingdoms_test"


def test_get_async_database_uses_env_db_name() -> None:
    assert db.get_async_database().name == "kingdoms_test"


def test_get_sync_database_name_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGO_DB", raising=False)
    assert db.get_database().name == "kingdoms"


def test_get_async_database_name_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MONGO_DB", raising=False)
    assert db.get_async_database().name == "kingdoms"
