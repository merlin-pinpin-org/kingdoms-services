"""ModRegistry: the catalog of installed mods and their declarations.

Reads mod declarations from ``config/mods/*.yaml`` at startup. It contains
no game logic: it answers "which mods exist, which are enabled, and what do
they declare?". ChannelService/RoleService consume these declarations to
provision channels and roles automatically.

Reference: kingdoms-services#26, docs/architecture/mods.md (kingdoms repo).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from kingdoms.core.services.mod_definition import (
    ChannelAccessPolicy,
    ChannelCategoryDef,
    ModDefinition,
    RoleDef,
)

logger = logging.getLogger(__name__)

ModDefs = dict[str, ModDefinition]


def _parse_str_list(declared: object, label: str, source: Path) -> list[str]:
    """Parse a string-list section of a mod declaration."""
    if not isinstance(declared, list) or any(not isinstance(v, str) for v in declared):
        raise ValueError(f"{source}: '{label}' must be a list of strings")
    return list(declared)


def _parse_channel_categories(declared: object, source: Path) -> list[ChannelCategoryDef]:
    """Parse the 'channels' section of a mod declaration."""
    if not isinstance(declared, list):
        raise ValueError(f"{source}: 'channels' must be a list")
    categories: list[ChannelCategoryDef] = []
    for entry in declared:
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: each channel entry must be a mapping")
        key = entry.get("key")
        display = entry.get("display_name")
        if not isinstance(key, str) or not key or not isinstance(display, str) or not display:
            raise ValueError(f"{source}: channel entries need 'key' and 'display_name'")
        access_data = entry.get("access")
        access = (
            ChannelAccessPolicy.from_dict(access_data)
            if isinstance(access_data, dict)
            else ChannelAccessPolicy()
        )
        categories.append(
            ChannelCategoryDef(
                key=key,
                display_name=display,
                description=str(entry.get("description", "")),
                per_instance=bool(entry.get("per_instance", False)),
                access=access,
            )
        )
    return categories


def _parse_roles(declared: object, source: Path) -> list[RoleDef]:
    """Parse the 'roles' section of a mod declaration."""
    if not isinstance(declared, list):
        raise ValueError(f"{source}: 'roles' must be a list")
    roles: list[RoleDef] = []
    for entry in declared:
        if not isinstance(entry, dict):
            raise ValueError(f"{source}: each role entry must be a mapping")
        key = entry.get("key")
        display = entry.get("display_name")
        if not isinstance(key, str) or not key or not isinstance(display, str) or not display:
            raise ValueError(f"{source}: role entries need 'key' and 'display_name'")
        roles.append(
            RoleDef(
                key=key,
                display_name=display,
                color=int(entry.get("color", 0x99AAB5)),
                hoisted=bool(entry.get("hoisted", False)),
                description=str(entry.get("description", "")),
                per_season=bool(entry.get("per_season", False)),
            )
        )
    return roles


def _parse_mod_yaml(data: dict[str, object], source: Path) -> ModDefinition:
    """Parse one mod YAML declaration into a ModDefinition."""
    name = data.get("id")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: missing 'id'")
    if not name.replace("_", "").replace("-", "").isalnum() or ":" in name:
        raise ValueError(f"{source}: mod id '{name}' must be a simple slug (no ':')")

    categories = _parse_channel_categories(data.get("channels", []), source)
    roles = _parse_roles(data.get("roles", []), source)

    workflows = _parse_str_list(data.get("workflows", []), "workflows", source)
    commands = _parse_str_list(data.get("commands", []), "commands", source)
    dependencies = _parse_str_list(data.get("dependencies", []), "dependencies", source)
    seasonal = data.get("seasonal")
    if not isinstance(seasonal, bool):
        raise ValueError(f"{source}: 'seasonal: true|false' is mandatory (mod type: seasonal or permanent)")

    private = bool(data.get("private", False))
    raw_owners = data.get("owner_discord_ids", [])
    if raw_owners is None:
        raw_owners = []
    if not isinstance(raw_owners, list):
        raise ValueError(f"{source}: 'owner_discord_ids' must be a list of Discord user ids")
    owner_ids = tuple(str(o).strip() for o in raw_owners if str(o).strip().isdigit())
    if private and not owner_ids:
        raise ValueError(
            f"{source}: a private mod must declare 'owner_discord_ids' (Discord user ids)"
        )

    return ModDefinition(
        name=name,
        enabled=bool(data.get("enabled", True)),
        seasonal=seasonal,
        private=private,
        owner_ids=owner_ids,
        channel_categories=tuple(categories),
        roles=tuple(roles),
        workflows=tuple(workflows),
        commands=tuple(commands),
        dependencies=tuple(dependencies),
    )


def _load_declaration(path: Path) -> ModDefinition:
    """Parse and validate one mod YAML declaration."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: declaration must be a mapping")
    return _parse_mod_yaml(data, path)


def load_mod_definitions(config_dir: Path, repo_root: Path | None = None) -> ModDefs:
    """Load and validate every mod declaration, legacy and modular.

    Legacy: ``<config_dir>/mods/*.yaml``. Modular (kingdoms#178):
    ``<repo_root>/modules/mods/*/mod.yaml`` — one self-contained dir per
    mod. Legacy wins on id collision (the moving branch keeps priority
    until its mechanical move lands).

    An invalid declaration raises: a broken mod must fail startup loudly,
    never load half-initialized (docs/architecture/mods.md).
    """
    definitions: ModDefs = {}
    if repo_root is not None:
        modular_dir = Path(repo_root) / "modules" / "mods"
        if modular_dir.is_dir():
            for mod_dir in sorted(p for p in modular_dir.iterdir() if p.is_dir()):
                declaration = mod_dir / "mod.yaml"
                if not declaration.is_file():
                    continue
                definition = _load_declaration(declaration)
                definitions[definition.name] = definition
                logger.info(
                    "Mod declared (modular): %s (enabled=%s, %d channels, %d roles)",
                    definition.name,
                    definition.enabled,
                    len(definition.channel_categories),
                    len(definition.roles),
                )
    mods_dir = config_dir / "mods"
    if mods_dir.is_dir():
        for path in sorted(mods_dir.glob("*.yaml")):
            definition = _load_declaration(path)
            definitions[definition.name] = definition
            logger.info(
                "Mod declared: %s (enabled=%s, %d channels, %d roles)",
                definition.name,
                definition.enabled,
                len(definition.channel_categories),
                len(definition.roles),
            )
    return definitions


class ModRegistry:
    """Runtime catalog of installed mods; populated at bot startup."""

    def __init__(self, definitions: ModDefs | None = None) -> None:
        self._mods: ModDefs = dict(definitions or {})

    def register(self, definition: ModDefinition) -> None:
        """Register a mod declaration (idempotent, last wins)."""
        self._mods[definition.name] = definition

    def get(self, mod_name: str) -> ModDefinition | None:
        """Return a mod definition by name, or None."""
        return self._mods.get(mod_name)

    def require(self, mod_name: str) -> ModDefinition:
        """Return a mod definition, fail loudly if not declared."""
        definition = self._mods.get(mod_name)
        if definition is None:
            raise KeyError(f"Mod '{mod_name}' is not declared in config/mods/")
        return definition

    def enabled(self) -> dict[str, ModDefinition]:
        """All enabled mod definitions."""
        return {name: d for name, d in self._mods.items() if d.enabled}

    def all(self) -> dict[str, ModDefinition]:
        """All declared mod definitions."""
        return dict(self._mods)
