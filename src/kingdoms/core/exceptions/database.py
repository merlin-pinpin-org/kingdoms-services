"""Database exceptions (kingdoms-services#10).

Persistence failures across the store seams (MongoDB, Redis, the
in-memory test stores). The taxonomy names the collection so the
audit carries the blast radius.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.exceptions.base import ErrorContext, KingdomsError

__all__ = [
    "ConnectionError",
    "DatabaseError",
    "DuplicateKeyError",
    "QueryError",
]


class DatabaseError(KingdomsError):
    """Base error for database failures."""

    code = "DATABASE_ERROR"

    def __init__(self, message: str, collection: str = "") -> None:
        """Create the error, naming the collection."""
        context = ErrorContext().extend(collection=collection) if collection else ErrorContext()
        super().__init__(message, context)


class ConnectionError(DatabaseError):
    """Raised when a database connection fails.

    Deliberately shadows :class:`builtins.ConnectionError` inside the
    package namespace: the builtin means a network socket fault; this
    one means the persistence layer is unreachable.
    """

    def __init__(self, message: str = "Database connection failed") -> None:
        """Create the error."""
        super().__init__(message)


class QueryError(DatabaseError):
    """Raised when a database query fails."""

    def __init__(self, message: str, query: str = "") -> None:
        """Create the error, naming the query shape."""
        super().__init__(message)
        if query:
            self.context = self.context.extend(query=query)


class DuplicateKeyError(DatabaseError):
    """Raised when a duplicate-key violation occurs."""

    def __init__(self, collection: str, key: str, value: Any) -> None:
        """Create the error, naming the offending key and value."""
        super().__init__(f"Duplicate key '{key}' with value '{value}' in collection '{collection}'", collection)
        self.context = self.context.extend(key=key, value=str(value))
