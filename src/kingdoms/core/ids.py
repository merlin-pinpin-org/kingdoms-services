"""Visible id builders and footers — the platform's single convention.

Every entity the designers manipulate carries a **visible, stable id**
built here, from documented parts, never from timestamps or magic
words. The prefix of every key is the mod's name (the ladder is a mod
like any other); uniqueness is always the embedded key pair (never the
name):

- ladder: ``ladder:<guild id>:<game key>``
- season: ``<ladder id>:<index>`` (index incremental from 1)
- mod season scope: ``<mod key>:<game key>:<guild id>``
- territory: ``territory:<season id>:<map key>``

Footers surface these ids on Discord panels: one small-text line
(``-#``) so designers can quote an id to the bot in seeding and admin
commands. Mods must build ids and footers through this module only.
"""

from __future__ import annotations

import re

LADDER_PREFIX = "ladder"
TERRITORY_PREFIX = "territory"


def ladder_id(game_key: str, guild_id: str) -> str:
    """Build the visible ladder id: ``ladder:<guild id>:<game key>``.

    ``ladder`` is the mod's name — the prefix of every core key is the
    mod name, the ladder being a mod like any other.
    """
    return f"{LADDER_PREFIX}:{guild_id}:{game_key}"


def season_id(ladder_id: str, index: int) -> str:
    """Build the visible season id: ``<ladder id>:<index>`` (from 1)."""
    return f"{ladder_id}:{index}"


def mod_scope(mod_key: str, game_key: str, guild_id: str) -> str:
    """Build a mod's season scope: ``<mod>:<game key>:<guild id>``."""
    return f"{mod_key}:{game_key}:{guild_id}"


def territory_id(season_id: str, map_key: str) -> str:
    """Build the visible territory id: ``territory:<season id>:<map key>``."""
    return f"{TERRITORY_PREFIX}:{season_id}:{map_key}"


def footer(*ids: str) -> str:
    """Render the ids footer line for a panel (empty ids are dropped)."""
    parts = [part for part in ids if part]
    return "-# " + " · ".join(f"`{part}`" for part in parts) if parts else ""


_NON_ALPHA_RE = re.compile(r"[^a-z0-9]+")


def scope_view_key(scope: str, slug: str) -> str:
    """Build a scope's view id: ``view:<short scope>:<slug>``.

    The short scope is the scope's **last** segment — ``games/aoe2``
    reads ``aoe2``, ``mods/ladder`` reads ``ladder`` — so view ids
    stay flat and quotable: ``view:aoe2:home``, ``view:ladder:news``.
    """
    short = scope.rsplit("/", 1)[-1] if "/" in scope else scope
    return f"view:{short}:{slug}"


def view_key(scope: str, slug: str, legacy: str = "") -> tuple[str, str]:
    """Return a persistent view's registry key pair: (current, legacy).

    The convention is ``view:<scope>:<slug>`` — the scope is the
    surface's perimeter (``home``, ``bot``, ``aoe2``, ``ladder``...),
    the slug the surface itself: ``view:home``, ``view:bot:admin``,
    ``view:aoe2:live``, ``view:ladder:news``. The id is displayed as
    the panel's footer so designers can quote it. The legacy key is
    what the view was registered under before the convention — empty
    when there was none; callers resolve the current key first.
    """
    return f"view:{scope}:{slug}", legacy


def map_entry_id(game_key: str, name: str, owner_guild_id: str | None) -> str:
    """Return a map entry's stable id, game-scoped and ownership-scoped.

    ``map:<game_key>:public:<slug>`` for the global catalog (config
    seed, bot admins), ``map:<game_key>:<guild_id>:<slug>`` for a
    guild's local maps. The game lives in the id itself — the same
    map name across games never collides — and the visibility scope
    is what every consumer (forums, pools, permissions) branches on.
    ``<slug>`` keeps underscores (``slug_id``).
    """
    scope = owner_guild_id if owner_guild_id else "public"
    return f"map:{game_key}:{scope}:{slug_id(name)}"


def pool_entry_id(game_key: str, name: str, owner_guild_id: str | None) -> str:
    """Return a map pool's stable id, game-scoped like the maps.

    ``pool:<game_key>:<guild_id>:<name-slug>`` for a guild's pool,
    ``pool:<game_key>:public:<name-slug>`` for a platform-wide one.
    Same convention as ``map_entry_id`` — the id carries the game and
    the visibility scope.
    """
    scope = owner_guild_id if owner_guild_id else "public"
    return f"pool:{game_key}:{scope}:{slug_id(name)}"


def slug_id(name: str) -> str:
    """Normalize a human name into its stable slug (ids, everywhere).

    Lowercase, every non-alphanumeric run collapsed to one underscore:
    ``Fish n Fish`` -> ``fish_n_fish``, ``Coast to Mountain`` ->
    ``coast_to_mountain``. Uniform across the catalog, the content
    store and the Discord footers.
    """
    return _NON_ALPHA_RE.sub("_", name.strip().lower()).strip("_") or "_"
