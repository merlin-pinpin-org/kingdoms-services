"""Modular mod layout resolution (kingdoms#178, kingdoms-services#211).

A mod lives in ONE self-contained directory::

    modules/mods/<mod>/
      mod.yaml            declaration (schema of config/mods/<mod>.yaml)
      src/<mod>/          python package imported as ``kingdoms.mods.<mod>``
      locales/<loc>.yaml  per-mod locale catalogs (merged into MessageCatalog)
      tests/              mod-owned tests
      docs/               mod docs pointer (canonical docs: kingdoms repo)

The legacy layout stays supported and takes precedence per mod::

    config/mods/<mod>.yaml + src/kingdoms/mods/<mod>/ + config/locales/*.yaml

so existing branches (ladder on ``vibe/merlin/main``, kingdoms on
``vibe/drasah/main``) keep working until their mechanical move
(one PR per mod, CI green each time).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MODULAR_ROOT = "modules/mods"


@dataclass(frozen=True, slots=True)
class ModLayout:
    """Where one mod's artifacts live."""

    name: str
    declaration: Path
    package: Path | None  # None: legacy kingdoms.mods.<name> import
    locale_files: tuple[Path, ...]

    @property
    def modular(self) -> bool:
        """True when the mod lives under modules/mods/."""
        return self.package is not None


def _modular_mod_dir(repo_root: Path, name: str) -> Path:
    return repo_root / MODULAR_ROOT / name


def find_mod_declaration(repo_root: Path, name: str) -> ModLayout:
    """Resolve one mod's layout: legacy config first, then modular dir.

    Raises FileNotFoundError when the mod is declared nowhere — a declared
    mod without a declaration is a broken install and must fail loudly
    (docs/architecture/mods.md).
    """
    root = Path(repo_root)
    legacy = root / "config" / "mods" / f"{name}.yaml"
    if legacy.is_file():
        return ModLayout(name=name, declaration=legacy, package=None, locale_files=())
    modular_yaml = _modular_mod_dir(root, name) / "mod.yaml"
    if modular_yaml.is_file():
        mod_dir = modular_yaml.parent
        locales_dir = mod_dir / "locales"
        locale_files = (
            tuple(sorted(locales_dir.glob("*.yaml"))) if locales_dir.is_dir() else ()
        )
        return ModLayout(
            name=name,
            declaration=modular_yaml,
            package=mod_dir / "src" / name,
            locale_files=locale_files,
        )
    raise FileNotFoundError(
        f"Mod '{name}' is not declared: no config/mods/{name}.yaml and no "
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
            if (mod_dir / "mod.yaml").is_file() and mod_dir.name not in layouts:
                layouts[mod_dir.name] = find_mod_declaration(root, mod_dir.name)
    return layouts
