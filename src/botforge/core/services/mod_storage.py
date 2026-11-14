"""The mods' storage facade: the only door to a mod's data (R8's seam).

A mod never touches a raw Mongo collection or a raw Redis key (the R8
architecture rule): it declares its collection names here and gets them
NAMESPACED under its own mod id — ``kingdoms:realms`` never collides
with another mod's ``realms``, and the platform can find every
document a mod owns (the install/uninstall data handling, #374).

The cache side is the same contract: a mod's state rides the shared
StateService under a mod-scoped prefix, so a mod's keys are enumerable
and wipeable without touching anyone else's.
"""

from __future__ import annotations

import re
from typing import Any

from botforge.core.services.state import StateService

_COLLECTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")


def _validate_collection_name(name: str) -> str:
    """Validate one collection name (a small slug, no dots, no prefix)."""
    if ":" in name or not _COLLECTION_NAME_RE.match(name):
        raise ValueError(
            f"invalid mod collection name {name!r}: a small slug "
            "[a-z][a-z0-9_], no ':' (the mod namespace adds the prefix)"
        )
    return name


class ModStorage:
    """A mod's namespaced door to the shared database.

    The collections are created on demand under ``<mod>:<name>``: the
    mod id is the namespace, the platform's tooling (the Danger Zone's
    per-mod data wipe, #374's uninstall-without-data) addresses them by
    prefix without knowing the mod's internals.
    """

    def __init__(self, database: Any, mod: str) -> None:
        """Bind the shared database and the mod's namespace."""
        self._db = database
        self._mod = mod

    @property
    def mod(self) -> str:
        """Return the mod's namespace (its id)."""
        return self._mod

    def collection(self, name: str) -> Any:
        """Return the namespaced collection handle for one declared name."""
        return self._db[f"{self._mod}:{_validate_collection_name(name)}"]

    def collection_id(self, name: str) -> str:
        """Return the full collection id (the prefix form, for tooling)."""
        return f"{self._mod}:{_validate_collection_name(name)}"

    async def wipe(self) -> int:
        """Delete every document of every namespaced collection; return the count.

        The per-mod data wipe (the uninstall-with-data path, #374): the
        namespace prefix makes it surgical — nothing outside the mod is
        touched.
        """
        total = 0
        for name in await self._db.list_collection_names():
            if name.startswith(f"{self._mod}:"):
                result = await self._db[name].delete_many({})
                total += int(result.deleted_count)
        return total


class ModStateCache:
    """A mod's scoped door to the shared Redis state.

    Every entry rides the StateService under the ``mod:<mod>:`` scope:
    the mod's keys are namespaced by construction and never collide
    with the core's or another mod's.
    """

    SCOPE_PREFIX = "mod"

    def __init__(self, state: StateService, mod: str) -> None:
        """Bind the shared state service and the mod's scope."""
        self._state = state
        self._scope = f"{self.SCOPE_PREFIX}:{mod}"

    @property
    def scope(self) -> str:
        """Return the mod's state scope."""
        return self._scope

    async def get(self, key: str) -> dict[str, Any] | None:
        """Read one mod state entry (None when unset or expired)."""
        return await self._state.get_state(self._scope, key)

    async def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """Write one mod state entry (optional TTL)."""
        return await self._state.set_state(self._scope, key, value, ttl=ttl)

    async def delete(self, key: str) -> bool:
        """Delete one mod state entry; unknown keys are a no-op."""
        return await self._state.delete_state(self._scope, key)


__all__ = ["ModStateCache", "ModStorage"]
