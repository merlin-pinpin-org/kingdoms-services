"""Visible id builders and footers — the platform's single convention.

Every entity the designers manipulate carries a **visible, stable id**
built here, from documented parts, never from timestamps or magic
words. Uniqueness is always the embedded key pair (never the name):

- ladder: ``ladder-<game key>-<guild id>``
- season: ``<ladder id>-<index>`` (index incremental from 1)
- mod season scope: ``<mod key>-<game key>-<guild id>``
- territory: ``territory:<season id>:<map key>``

Footers surface these ids on Discord panels: one small-text line
(``-#``) so designers can quote an id to the bot in seeding and admin
commands. Mods must build ids and footers through this module only.
"""

from __future__ import annotations

LADDER_PREFIX = "ladder"
TERRITORY_PREFIX = "territory"


def ladder_id(game_key: str, guild_id: str) -> str:
    """Build the visible ladder id: ``ladder-<game key>-<guild id>``."""
    return f"{LADDER_PREFIX}-{game_key}-{guild_id}"


def season_id(ladder_id: str, index: int) -> str:
    """Build the visible season id: ``<ladder id>-<index>`` (from 1)."""
    return f"{ladder_id}-{index}"


def mod_scope(mod_key: str, game_key: str, guild_id: str) -> str:
    """Build a mod's season scope: ``<mod>-<game key>-<guild id>``."""
    return f"{mod_key}-{game_key}-{guild_id}"


def territory_id(season_id: str, map_key: str) -> str:
    """Build the visible territory id: ``territory:<season id>:<map key>``."""
    return f"{TERRITORY_PREFIX}:{season_id}:{map_key}"


def footer(*ids: str) -> str:
    """Render the ids footer line for a panel (empty ids are dropped)."""
    parts = [part for part in ids if part]
    return "-# " + " · ".join(f"`{part}`" for part in parts) if parts else ""
