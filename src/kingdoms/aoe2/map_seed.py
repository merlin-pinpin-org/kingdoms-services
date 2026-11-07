"""The map seed: the fields a provider resolves for one map name.

AoE2 game data (kingdoms.aoe2, owner direction 2026-11-05) — shared by
the game service and the import flows, never core plumbing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MapSeed:
    """The fields a provider resolves for one map name."""

    name: str
    description: str
    resource_url: str
    image_url: str
    icon_url: str = ""
    terrain: str = ""
    size: str = ""
    map_type: str = ""
    nomad: str = ""
    walls: str = ""
    tc: str = ""
    player_capacity: str = ""
    creator: str = ""
    public: bool = True
    game_versions: tuple[str, ...] = ()
    versions: tuple[dict[str, object], ...] = ()
    tags: tuple[str, ...] = ()


class LiquipediaUnconfiguredError(RuntimeError):
    """The liquipedia provider is not configured — a functional gap."""


class LiquipediaCircuitOpenError(RuntimeError):
    """The liquipedia circuit is open: the API is rate-limiting this IP.

    Every call fails fast while the window is open — no HTTP traffic.
    Retry later; the window doubles on each consecutive opening.
    """
