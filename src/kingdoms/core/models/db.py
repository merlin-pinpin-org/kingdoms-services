"""MongoDB client and database setup for Kingdoms.

Singleton sync (``MongoClient``) and async (``AsyncMongoClient``, the native
pymongo async API available since pymongo 4.9 — no Motor dependency) clients,
configured from the ``MONGO_URI`` and ``MONGO_DB`` environment variables.
Reference: kingdoms-services#4 and ADR-0004 (MongoDB schema design).
"""

from __future__ import annotations

import os

from pymongo import MongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.asynchronous.mongo_client import AsyncMongoClient
from pymongo.database import Database

Document = dict[str, object]

DEFAULT_MONGO_URI = "mongodb://localhost:27017"
DEFAULT_MONGO_DB = "kingdoms"

_sync_client: MongoClient[Document] | None = None
_async_client: AsyncMongoClient[Document] | None = None


def _mongo_uri() -> str:
    """Read the MongoDB URI from the environment."""
    return os.environ.get("MONGO_URI", DEFAULT_MONGO_URI)


def _mongo_db_name() -> str:
    """Read the MongoDB database name from the environment."""
    return os.environ.get("MONGO_DB", DEFAULT_MONGO_DB)


def get_sync_client() -> MongoClient[Document]:
    """Get or create the synchronous MongoDB client."""
    global _sync_client
    if _sync_client is None:
        _sync_client = MongoClient(_mongo_uri())
    return _sync_client


def get_async_client() -> AsyncMongoClient[Document]:
    """Get or create the asynchronous MongoDB client."""
    global _async_client
    if _async_client is None:
        _async_client = AsyncMongoClient(_mongo_uri())
    return _async_client


def get_database() -> Database[Document]:
    """Get the MongoDB database (sync)."""
    return get_sync_client()[_mongo_db_name()]


def get_async_database() -> AsyncDatabase[Document]:
    """Get the MongoDB database (async)."""
    return get_async_client()[_mongo_db_name()]


def close_sync_client() -> None:
    """Close the synchronous MongoDB client, if it was created."""
    global _sync_client
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None


async def close_async_client() -> None:
    """Close the asynchronous MongoDB client, if it was created."""
    global _async_client
    if _async_client is not None:
        await _async_client.close()
        _async_client = None
