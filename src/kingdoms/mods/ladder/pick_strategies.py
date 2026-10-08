"""The ladder's map-pick strategies: named, registered, admin-swappable (#223).

How a match's map is chosen is **configuration, not code**: each mode is
a small strategy class registered under a stable key; the ladder stores
the active key in its settings (``pick_strategy``), and the admin menu
swaps it with a select — no redeploys, no branching call sites. Adding a
mode is one class plus one ``register_pick_strategy`` call.

Contract: a strategy receives the pool's candidate map ids and both
players' fav/ban snapshots, and returns one map id. The service owns
the rng (injected, testable) — strategies stay pure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PickContext:
    """Everything a pick strategy may look at for one match."""

    candidates: tuple[str, ...]
    host_favs: tuple[str, ...]
    host_bans: tuple[str, ...]
    guest_favs: tuple[str, ...]
    guest_bans: tuple[str, ...]
    rng: random.Random


class PickStrategy(Protocol):
    """One map-pick mode: pick a map id from the context."""

    key: str
    label: str

    def pick(self, ctx: PickContext) -> str:
        """Return exactly one of ``ctx.candidates``."""
        ...


class RandomStrategy:
    """Pure chance: uniform over the pool, preferences ignored."""

    key = "random"
    label = "Hasard pur (prefs ignorées)"

    def pick(self, ctx: PickContext) -> str:
        """Return one uniform candidate."""
        return ctx.rng.choice(ctx.candidates)


class BanFilterStrategy:
    """Remove both players' bans, uniform over what remains."""

    key = "ban_filter"
    label = "Sans les bans"

    def pick(self, ctx: PickContext) -> str:
        """Return one non-banned candidate (uniform)."""
        allowed = [m for m in ctx.candidates if m not in set(ctx.host_bans) | set(ctx.guest_bans)]
        if not allowed:
            return ctx.rng.choice(ctx.candidates)
        return ctx.rng.choice(allowed)


class WeightedStrategy:
    """Ban-safe fav weighting: bans removed, each fav adds weight (#223 default)."""

    key = "weighted"
    label = "Favoris pondérés (sans bans)"

    def pick(self, ctx: PickContext) -> str:
        """Return the fav-weighted candidate among the non-banned ones."""
        allowed = [m for m in ctx.candidates if m not in set(ctx.host_bans) | set(ctx.guest_bans)]
        if not allowed:
            raise ValueError("every pool map is banned for this match")
        favs = ctx.host_favs + ctx.guest_favs
        weights = [1 + favs.count(mid) for mid in allowed]
        return ctx.rng.choices(allowed, weights=weights, k=1)[0]


_REGISTERED: dict[str, PickStrategy] = {}
_DEFAULT_KEY = WeightedStrategy.key


def register_pick_strategy(strategy: PickStrategy) -> None:
    """Register one mode under its key (idempotent, last wins)."""
    _REGISTERED[strategy.key] = strategy


def list_pick_strategies() -> list[PickStrategy]:
    """List the registered modes, sorted by key (admin select order)."""
    return [_REGISTERED[key] for key in sorted(_REGISTERED)]


def resolve_pick_strategy(key: str) -> PickStrategy:
    """Resolve a configured key; unknown keys fall back to the default."""
    if key in _REGISTERED:
        return _REGISTERED[key]
    return _REGISTERED[_DEFAULT_KEY]


for _strategy in (RandomStrategy(), BanFilterStrategy(), WeightedStrategy()):
    register_pick_strategy(_strategy)
