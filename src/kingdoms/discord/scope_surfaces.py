"""Scope surfaces: the extensible registry of a scope's mirror syncs.

The sync button of an admin menu re-creates everything its scope owns:
the declared channels (the tree provisioning), every pinned view
registered for the scope, and the scope's data-mirror forums (maps,
pools, civs). Nothing runs in the background anymore — the surfaces
heal on demand, at the button's click.

Extensibility: a module registers its sync through
:func:`register_scope_sync` (``scope`` + an async ``sync(guild_id,
bot) -> int``); the button discovers every sync whose scope is the
clicked scope or one of its descendants — ``games`` runs the aoe2
forums, ``games/aoe2`` runs them too, ``mods`` runs none until a mod
declares one. No hard-coded list anywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("kingdoms.scope_surfaces")

ScopeSync = Callable[[str, Any], Awaitable[int]]

_REGISTRY: dict[str, list[ScopeSync]] = {}


def register_scope_sync(scope: str, sync: ScopeSync) -> None:
    """Declare one scope's mirror sync (idempotent per function)."""
    if sync not in _REGISTRY.setdefault(scope, []):
        _REGISTRY[scope].append(sync)


def _descendant_prefixes(scope: str) -> tuple[str, ...]:
    """Return the scope and every ancestor prefix (games/aoe2 -> both)."""
    parts = scope.split("/")
    return tuple("/".join(parts[: i + 1]) for i in range(len(parts)))


async def run_scope_syncs(guild_id: str, bot: Any, scope: str) -> int:
    """Run every registered sync of the scope and its descendants."""
    actions = 0
    for prefix in _descendant_prefixes(scope):
        for sync in _REGISTRY.get(prefix, ()):
            try:
                actions += await sync(guild_id, bot)
            except Exception:
                logger.warning("scope sync failed (%s, guild %s) — best-effort", prefix, guild_id, exc_info=True)
    return actions


def clear_scope_syncs() -> None:
    """Empty the registry (tests only)."""
    _REGISTRY.clear()


__all__ = ["clear_scope_syncs", "register_scope_sync", "run_scope_syncs"]
