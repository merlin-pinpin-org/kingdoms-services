"""Mod entrypoint seam: how the core discovers and runs installed mods.

The core never imports a mod by name. For every enabled mod it resolves
the layout (mod_layout) and loads the mod's TWO §7 concerns separately:

- ``botforge.mods.<id>.cogs`` — the Discord layer: commands, views, home
  sections, admin panel sections, background tasks. The bot loads the cog
  package and calls its optional lifecycle hooks:

  - ``register(bot, config)`` — wire the cog onto the bot. Called once at
    bot build time, before the command tree sync.
  - ``setup_hook(bot)`` — re-register persistent UI after a restart
    (dynamic items, view classes). Called from ``setup_hook``.
  - ``close(bot)`` — cancel background tasks. Called from ``close``.

- ``botforge.mods.<id>.grpc`` — the mod's gRPC micro-service package: its
  data, its collections, its contract. The COG calls the service through
  the contract (``mod_service``); the service never touches Discord.

Every hook is optional: a mod with no Discord surface declares nothing.
Hooks must be idempotent and degrade quietly when their stack (Mongo,
Redis, providers) is not configured — the same contract as the core
wiring builders.

The pre-§7 layouts keep their single-package behavior: the package
imports as ``botforge.mods.<id>`` and carries the hooks itself.

Reference: docs/architecture/mods.md §7 (botforge repo) — the cog is the
only Discord layer of a mod, the core owns the machinery.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from botforge.core.services.mod_layout import MODULAR_ROOT, find_mod_declaration

logger = logging.getLogger(__name__)


class ModHooks(Protocol):
    """The optional lifecycle surface of a mod's cog package."""

    def register(self, bot: Any, config: Any) -> None:
        """Wire the mod onto the bot (services, commands, registries)."""
        ...

    def setup_hook(self, bot: Any) -> None:
        """Re-register persistent UI after a restart."""
        ...

    def close(self, bot: Any) -> None:
        """Stop the mod's background tasks (best-effort)."""
        ...


@dataclass
class LoadedMod:
    """One enabled mod with its cog package and optional hooks bound."""

    name: str
    package: Any
    has_register: bool
    has_setup_hook: bool
    has_close: bool
    service: Any | None = None
    has_service: bool = field(default=False)


def _load_package_at(dotted: str, package_dir: Path) -> Any:
    """Import a directory-package under a dotted name, file-based.

    The §7 layout never touches ``sys.path``: the cog and the service
    packages are loaded from their real paths and registered under the
    ``botforge.mods.<name>.*`` names — one module object per name, the
    same one-object rule as the legacy alias.
    """
    init_file = package_dir / "__init__.py"
    if not init_file.is_file():
        raise ImportError(f"no package at {package_dir} (missing __init__.py)")
    parent_name, _, _ = dotted.rpartition(".")
    if parent_name and parent_name not in sys.modules:
        parent = importlib.util.module_from_spec(
            importlib.machinery.ModuleSpec(parent_name, None, is_package=True)
        )
        parent.__path__ = []
        sys.modules[parent_name] = parent
    if dotted in sys.modules:
        return sys.modules[dotted]
    spec = importlib.util.spec_from_file_location(
        dotted, init_file, submodule_search_locations=[str(package_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {dotted} from {package_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[dotted] = module
    spec.loader.exec_module(module)
    return module


def _load_legacy_package(name: str, repo_root: Path | None) -> Any:
    """Import a pre-§7 mod package as ``botforge.mods.<name>``."""
    if repo_root is not None:
        modular_src = Path(repo_root) / MODULAR_ROOT / name / "src"
        if modular_src.is_dir():
            if str(modular_src) not in sys.path:
                sys.path.insert(0, str(modular_src))
            importlib.import_module("botforge.mods")
            if f"botforge.mods.{name}" not in sys.modules:
                sys.modules[f"botforge.mods.{name}"] = importlib.import_module(name)
    return importlib.import_module(f"botforge.mods.{name}")


def load_mod_package(name: str, repo_root: Path | None = None) -> LoadedMod:
    """Import the mod's cog package (and its service) and detect the hooks.

    Resolution (mod_layout): a §7 mod loads ``cogs/<name>/`` as
    ``botforge.mods.<name>.cogs`` and, when present, ``grpc/<name>/`` as
    ``botforge.mods.<name>.grpc``. A pre-§7 mod imports its single package
    as ``botforge.mods.<name>``. A missing package raises ImportError: a
    declared mod without its package is a broken install and must fail
    loudly, never silently disable.
    """
    if repo_root is not None:
        try:
            layout = find_mod_declaration(Path(repo_root), name)
        except FileNotFoundError:
            layout = None
        if layout is not None and layout.cogs is not None:
            # the mod's CORE package (src/<name>) registers as
            # botforge.mods.<name> FIRST — the cog and the grpc service
            # import their core through it (the layout's package field)
            if layout.package is not None and (layout.package / "__init__.py").is_file():
                _load_package_at(f"botforge.mods.{name}", layout.package)
            package = _load_package_at(f"botforge.mods.{name}.cogs", layout.cogs)
            service = None
            has_service = False
            if layout.service is not None and (layout.service / "__init__.py").is_file():
                service = _load_package_at(f"botforge.mods.{name}.grpc", layout.service)
                has_service = True
            return LoadedMod(
                name=name,
                package=package,
                has_register=callable(getattr(package, "register", None)),
                has_setup_hook=callable(getattr(package, "setup_hook", None)),
                has_close=callable(getattr(package, "close", None)),
                service=service,
                has_service=has_service,
            )
    package = _load_legacy_package(name, repo_root)
    return LoadedMod(
        name=name,
        package=package,
        has_register=callable(getattr(package, "register", None)),
        has_setup_hook=callable(getattr(package, "setup_hook", None)),
        has_close=callable(getattr(package, "close", None)),
    )


def mod_service(name: str, repo_root: Path | None = None) -> Any:
    """Return the mod's gRPC service package (``botforge.mods.<name>.grpc``).

    The seam the COG uses to call its service: the cog never imports the
    service's internals by path, it goes through this accessor (or the
    equivalent gRPC client) so the service stays swappable — in-process
    today, over the wire once the mod runs its own server.
    """
    if repo_root is not None:
        layout = find_mod_declaration(Path(repo_root), name)
        if layout.service is not None and (layout.service / "__init__.py").is_file():
            return _load_package_at(f"botforge.mods.{name}.grpc", layout.service)
    return importlib.import_module(f"botforge.mods.{name}.grpc")


def register_mod(bot: Any, config: Any, name: str, repo_root: Path | None = None) -> bool:
    """Run one mod's ``register`` hook; False when absent or failed.

    Failures are logged and swallowed: a broken mod degrades to
    "inactive" instead of taking the bot down — the same rule as the
    per-mod provisioning loop.
    """
    try:
        loaded = load_mod_package(name, repo_root=repo_root)
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


def run_mod_hook(bot: Any, name: str, hook: str, repo_root: Path | None = None) -> None:
    """Run one optional mod hook (``setup_hook`` / ``close``), best-effort."""
    try:
        loaded = load_mod_package(name, repo_root=repo_root)
    except Exception:
        return
    fn = getattr(loaded.package, hook, None)
    if not callable(fn):
        return
    try:
        fn(bot)
    except Exception:
        logger.exception("MOD %s %s hook failed — best-effort", name, hook)
