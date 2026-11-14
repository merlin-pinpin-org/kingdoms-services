"""Modular mod layout resolution (botforge-services#178, botforge-services#211).

A mod lives in ONE self-contained directory (docs/architecture/mods.md §7,
the target layout — the gRPC micro-service, its contract and the Discord
cog are SEPARATE concerns)::

    modules/mods/<mod>/
      conf/               declaration YAMLs: mod.yaml + locales/<loc>.yaml
      grpc/<mod>/         the mod's gRPC service package (its data, its
                          collections) + its contract (service.py, contract.py)
      cogs/<mod>/         the Discord cog package — loaded by the bot, calling
                          the mod's service through the contract
      tests/              mod-owned tests
      README.md           mod docs pointer (canonical docs: botforge repo)

The service imports as ``botforge.mods.<mod>.grpc``, the cog as
``botforge.mods.<mod>.cogs`` — the cog is the only Discord layer and never
touches another mod.

Two older layouts stay supported during the migration (legacy first on
collision, then the pre-§7 modular layout, then conf/):

- legacy: ``config/mods/<mod>.yaml`` + ``src/botforge/mods/<mod>/``
- pre-§7 modular: ``modules/mods/<mod>/mod.yaml`` + ``src/<mod>/`` +
  ``locales/`` — so existing branches (ladder on ``vibe/merlin/main``,
  kingdoms on ``vibe/drasah/main``) keep working until their mechanical
  move (one PR per mod, CI green each time).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MODULAR_ROOT = "modules/mods"


def default_repo_root() -> Path | None:
    """Return the repo root the package lives in, when it does.

    Walks up from this file to the first directory that looks like the
    repo (a pyproject.toml): an editable checkout and the Docker image
    (the sources at /app/src) both resolve; a wheel install resolves to
    None (the modular mods are not part of a wheel — §7's PyPI future
    ships them as their own artifacts).
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


@dataclass(frozen=True, slots=True)
class ModLayout:
    """Where one mod's artifacts live."""

    name: str
    declaration: Path
    package: Path | None  # pre-§7 modular src/<mod>; None: legacy botforge.mods.<name>
    locale_files: tuple[Path, ...]
    cogs: Path | None = None  # §7 cogs/<mod> — the Discord layer
    service: Path | None = None  # §7 grpc/<mod> — the mod's gRPC service package

    @property
    def modular(self) -> bool:
        """True when the mod lives under modules/mods/."""
        return self.package is not None or self.cogs is not None or self.service is not None


def _modular_mod_dir(repo_root: Path, name: str) -> Path:
    return repo_root / MODULAR_ROOT / name


def _locale_files(mod_dir: Path, *candidates: Path) -> tuple[Path, ...]:
    """Return the first existing locale directory's YAML files, sorted."""
    for locales_dir in candidates:
        if locales_dir.is_dir():
            return tuple(sorted(locales_dir.glob("*.yaml")))
    return ()


def find_mod_declaration(repo_root: Path, name: str) -> ModLayout:
    """Resolve one mod's layout: legacy config, then §7 conf/, then pre-§7 root.

    Raises FileNotFoundError when the mod is declared nowhere — a declared
    mod without a declaration is a broken install and must fail loudly
    (docs/architecture/mods.md).
    """
    root = Path(repo_root)
    legacy = root / "config" / "mods" / f"{name}.yaml"
    if legacy.is_file():
        return ModLayout(name=name, declaration=legacy, package=None, locale_files=())
    mod_dir = _modular_mod_dir(root, name)
    conf_yaml = mod_dir / "conf" / "mod.yaml"
    if conf_yaml.is_file():
        # the mod's CORE package (src/<name>) rides the layout when it
        # exists — the loader registers it as botforge.mods.<name> so
        # the cog and the grpc service import their core through it
        core_package = mod_dir / "src" / name
        return ModLayout(
            name=name,
            declaration=conf_yaml,
            package=core_package if (core_package / "__init__.py").is_file() else None,
            locale_files=_locale_files(mod_dir, mod_dir / "conf" / "locales"),
            cogs=mod_dir / "cogs" / name,
            service=mod_dir / "grpc" / name,
        )
    root_yaml = mod_dir / "mod.yaml"
    if root_yaml.is_file():
        locales = _locale_files(mod_dir, mod_dir / "locales")
        return ModLayout(
            name=name,
            declaration=root_yaml,
            package=mod_dir / "src" / name,
            locale_files=locales,
        )
    raise FileNotFoundError(
        f"Mod '{name}' is not declared: no config/mods/{name}.yaml, no "
        f"{MODULAR_ROOT}/{name}/conf/mod.yaml and no "
        f"{MODULAR_ROOT}/{name}/mod.yaml under {root}"
    )


def discover_mods(repo_root: Path) -> dict[str, ModLayout]:
    """Every declared mod, legacy and modular, by name (legacy wins on collision)."""
    root = Path(repo_root)
    layouts: dict[str, ModLayout] = {}
    legacy_dir = root / "config" / "mods"
    if legacy_dir.is_dir():
        for path in sorted(legacy_dir.glob("*.yaml")):
            layouts[path.stem] = ModLayout(
                name=path.stem, declaration=path, package=None, locale_files=()
            )
    modular_dir = root / MODULAR_ROOT
    if modular_dir.is_dir():
        for mod_dir in sorted(p for p in modular_dir.iterdir() if p.is_dir()):
            declared = (mod_dir / "conf" / "mod.yaml").is_file() or (mod_dir / "mod.yaml").is_file()
            if declared and mod_dir.name not in layouts:
                layouts[mod_dir.name] = find_mod_declaration(root, mod_dir.name)
    return layouts
