"""ModRegistry: the catalog of installed mods and their declarations.

Reads mod declarations from ``config/mods/*.yaml`` at startup. It contains
no game logic: it answers "which mods exist, which are enabled, and what do
they declare?". ChannelService/RoleService consume these declarations to
provision channels and roles automatically.

Reference: botforge-services#26, docs/architecture/mods.md (botforge repo).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from botforge.core.services.mod_definition import (
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


def _group_display(declared: object, group_ref: str, source: Path) -> str:
    """Resolve one channel's group ref to the group's display name."""
    if not group_ref or not isinstance(declared, list):
        return ""
    for group in declared:
        if isinstance(group, dict) and str(group.get("key", "")) == group_ref:
            display = group.get("display_name")
            return str(display) if isinstance(display, str) else ""
    raise ValueError(f"{source}: the channel's group {group_ref!r} is not declared in channel_groups")


def _parse_channel_categories(
    declared: object, groups: object, source: Path
) -> list[ChannelCategoryDef]:
    """Parse the 'channels' section of a mod declaration (its groups ride along)."""
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
        access = ChannelAccessPolicy.from_dict(access_data) if isinstance(access_data, dict) else ChannelAccessPolicy()
        # the group ref resolves to the group's display name (the
        # channel_groups section is the source of truth of the structure)
        group_ref = str(entry.get("group", "") or "")
        group_display = _group_display(groups, group_ref, source)
        admin_only = bool(entry.get("admin_only", False))
        categories.append(
            ChannelCategoryDef(
                group=group_display,
                admin_only=admin_only,
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

    categories = _parse_channel_categories(
        data.get("channels", []), data.get("channel_groups"), source
    )
    roles = _parse_roles(data.get("roles", []), source)
    # the SDK's standard role trio rides EVERY mod (owner direction
    # 2026-11-13): candidate (the signup needs validation), member (the
    # registered/validated), admin (the mod's operators) — all prefixed
    # with the mod's key, all provisioned with a persisted mapping (the
    # stored id is the resolver, the display name is renamable). A mod
    # overrides a standard role by declaring its own entry with the
    # same key.
    standard = {
        "candidate": f"{name.capitalize()} Candidate",
        "member": f"{name.capitalize()} Member",
        "admin": f"{name.capitalize()} Admin",
    }
    declared_keys = {role.key for role in roles}
    roles.extend(
        RoleDef(key=key, display_name=display)
        for key, display in standard.items()
        if key not in declared_keys
    )

    workflows = _parse_str_list(data.get("workflows", []), "workflows", source)
    commands = _parse_str_list(data.get("commands", []), "commands", source)
    dependencies = _parse_str_list(data.get("dependencies", []), "dependencies", source)
    required_games = _parse_str_list(data.get("required_games", []), "required_games", source)
    seasonal = data.get("seasonal")
    if not isinstance(seasonal, bool):
        raise ValueError(f"{source}: 'seasonal: true|false' is mandatory (mod type: seasonal or permanent)")

    default_active = bool(data.get("default_active", False))
    emoji = str(data.get("emoji", "") or "")
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
        default_active=default_active,
        emoji=emoji,
        channel_categories=tuple(categories),
        roles=tuple(roles),
        workflows=tuple(workflows),
        commands=tuple(commands),
        dependencies=tuple(dependencies),
        required_games=tuple(required_games),
    )


def _load_declaration(path: Path) -> ModDefinition:
    """Parse and validate one mod YAML declaration."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: declaration must be a mapping")
    return _parse_mod_yaml(data, path)


def load_mod_definitions(config_dir: Path, repo_root: Path | None = None) -> ModDefs:
    """Load and validate every mod declaration — legacy config, then modular.

    The legacy declarations (config/mods/*.yaml) come first and win on
    id collision; the modular §7 layout (modules/mods/<mod>/conf/mod.yaml,
    pre-§7 root mod.yaml) is discovered through mod_layout when a repo
    root is given. An invalid declaration raises: a broken mod must
    fail startup loudly, never load half-initialized
    (docs/architecture/mods.md).
    """
    mods_dir = config_dir / "mods"
    definitions: ModDefs = {}
    if repo_root is not None:
        from botforge.core.services.mod_layout import discover_mods

        for name, layout in sorted(discover_mods(Path(repo_root)).items()):
            if name in definitions:
                continue
            definitions[name] = _load_declaration(layout.declaration)
    if not mods_dir.is_dir():
        return definitions
    max_mods = 20
    for path in sorted(mods_dir.glob("*.yaml")):
        if path.name.endswith(".views.yaml"):
            continue  # a mod's views declaration, not a mod declaration
        if len(definitions) >= max_mods:
            raise ValueError(
                f"the platform carries at most {max_mods} mods ({mods_dir}); "
                "disable one before declaring another"
            )
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
