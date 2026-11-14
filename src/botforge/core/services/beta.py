"""The SDK's BETA phase — a dev phase where the rules bend (generic, per mod).

Every mod wants the same thing while it is being built: a phase where
one does not really play — the rules are twisted, the screens and the
flows are validated, the mechanics are exercised without a real
season at stake. The SDK owns the CONCEPT once (owner direction
2026-11-14); each mod owns what bending means for its own mechanics.

Two pieces:

- **The beta state**: a per-mod, per-guild flag (``beta`` in the mod's
  state cache) — a mod's service checks ``is_beta`` and decides what
  its rules bend into (skip a gate, freeze a timer, accept a test
  value...). The flag is the mod's to read; the phase is the
  operator's to enter and leave.
- **The beta console**: the mod's extensible action registry — like
  the admin console (``console.ConsoleAction``), but per mod and
  owned by the mod: each mod registers its own beta actions (a time
  machine, a distributor, a test realm...) and the mod's admin
  surface renders them (guarded, ephemeral, the 3-second rule).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("botforge.core.services.beta")


@dataclass(frozen=True, slots=True)
class BetaAction:
    """One beta action a mod registers: a mechanic twisted on demand.

    ``key`` is the mod-scoped action id (the button's custom id rides
    it); ``label_key`` the i18n catalog key; ``emoji`` the visual
    identity; ``handler(bot, guild_id)`` the twisting itself — it
    returns a short localized report the console shows back.
    """

    key: str
    label_key: str
    handler: Callable[[Any, str], Awaitable[str]]
    emoji: str = ""
    confirm: bool = False  # a destructive twist asks first


_BETA_ACTIONS: dict[tuple[str, str], BetaAction] = {}
_MODS_WITH_ACTIONS: dict[str, list[str]] = {}


def register_beta_action(mod: str, action: BetaAction) -> BetaAction:
    """Register one beta action for a mod (idempotent by key)."""
    _BETA_ACTIONS[(mod, action.key)] = action
    keys = _MODS_WITH_ACTIONS.setdefault(mod, [])
    if action.key not in keys:
        keys.append(action.key)
    logger.info("BETA: %s registers the %s action", mod, action.key)
    return action


def registered_beta_actions(mod: str) -> tuple[BetaAction, ...]:
    """List a mod's registered beta actions (the registration order)."""
    return tuple(_BETA_ACTIONS[(mod, key)] for key in _MODS_WITH_ACTIONS.get(mod, ()))


def beta_action(mod: str, key: str) -> BetaAction | None:
    """Return one registered beta action (the click-time lookup)."""
    return _BETA_ACTIONS.get((mod, key))


def _state_key(guild_id: str) -> str:
    """Return the beta flag's key inside the mod's state cache (guild-scoped)."""
    return f"beta:g:{guild_id}"


async def is_beta(cache: Any, guild_id: str, mod: str) -> bool:
    """Return True when the mod's beta phase is active on the guild.

    ``cache`` is the mod's ``ModStateCache`` (the SDK's namespaced
    state door); None degrades to False — a missing state service
    never bends the rules.
    """
    if cache is None:
        return False
    try:
        entry = await cache.get(_state_key(guild_id))
        return bool((entry or {}).get("active"))
    except Exception:
        logger.warning("BETA: the %s state unreadable — best-effort", mod, exc_info=True)
        return False


async def enter_beta(cache: Any, guild_id: str, mod: str) -> None:
    """Enter the beta phase: the rules bend from here."""
    if cache is not None:
        await cache.set(_state_key(guild_id), {"active": True})
    logger.info("BETA: %s enters the beta phase (guild %s)", mod, guild_id)


async def exit_beta(cache: Any, guild_id: str, mod: str) -> None:
    """Leave the beta phase: the rules straighten back."""
    if cache is not None:
        await cache.set(_state_key(guild_id), {"active": False})
    logger.info("BETA: %s leaves the beta phase (guild %s)", mod, guild_id)


__all__ = [
    "BetaAction",
    "beta_action",
    "enter_beta",
    "exit_beta",
    "is_beta",
    "register_beta_action",
    "registered_beta_actions",
]
