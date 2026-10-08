"""Mod entrypoint seam: how the core discovers and runs installed mods.

The core never imports a mod by name. For every enabled mod declared in
``config/mods/<id>.yaml``, it dynamically imports ``kingdoms.mods.<id>``
and, when present, calls its lifecycle hooks:

- ``register(bot, config)`` — wire the mod onto the bot (commands, home
  views, admin panel sections, background tasks). Called once at bot
  build time, before the command tree sync.
- ``setup_hook(bot)`` — re-register persistent UI after a restart
  (dynamic items, view classes). Called from ``setup_hook``.
- ``close(bot)`` — cancel background tasks. Called from ``close``.

Every hook is optional: a mod with no Discord surface declares nothing.
Hooks must be idempotent and degrade quietly when their stack (Mongo,
Redis, providers) is not configured — the same contract as the core
wiring builders.

Reference: docs/architecture/mods.md (kingdoms repo), the core/mods
split rule — mod code stays minimal, the core owns the machinery.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ModHooks(Protocol):
    """The optional lifecycle surface of a mod package."""

    def register(self, bot: Any, config: Any) -> None: ...
    def setup_hook(self, bot: Any) -> None: ...
    def close(self, bot: Any) -> None: ...


@dataclass
class LoadedMod:
    """One enabled mod package with its optional hooks bound."""

    name: str
    package: Any
    has_register: bool
    has_setup_hook: bool
    has_close: bool


def load_mod_package(name: str) -> LoadedMod:
    """Import ``kingdoms.mods.<name>`` and detect its optional hooks.

    A missing package raises ImportError: a declared mod without its
    package is a broken install and must fail loudly, never silently
    disable.
    """
    package = importlib.import_module(f"kingdoms.mods.{name}")
    return LoadedMod(
        name=name,
        package=package,
        has_register=callable(getattr(package, "register", None)),
        has_setup_hook=callable(getattr(package, "setup_hook", None)),
        has_close=callable(getattr(package, "close", None)),
    )


def register_mod(bot: Any, config: Any, name: str) -> bool:
    """Run one mod's ``register`` hook; False when absent or failed.

    Failures are logged and swallowed: a broken mod degrades to
    "inactive" instead of taking the bot down — the same rule as the
    per-mod provisioning loop.
    """
    try:
        loaded = load_mod_package(name)
    except Exception:
        logger.exception("MOD %s entrypoint import failed — mod stays inactive", name)
        return False
    if not loaded.has_register:
        logger.info("MOD %s has no register hook — nothing to wire", name)
        return False
    try:
        loaded.package.register(bot, config)
        logger.info("MOD %s registered", name)
        return True
    except Exception:
        logger.exception("MOD %s register hook failed — mod stays inactive", name)
        return False


def run_mod_hook(bot: Any, name: str, hook: str) -> None:
    """Run one optional mod hook (``setup_hook`` / ``close``), best-effort."""
    try:
        loaded = load_mod_package(name)
    except Exception:
        return
    fn = getattr(loaded.package, hook, None)
    if not callable(fn):
        return
    try:
        fn(bot)
    except Exception:
        logger.exception("MOD %s %s hook failed — best-effort", name, hook)
