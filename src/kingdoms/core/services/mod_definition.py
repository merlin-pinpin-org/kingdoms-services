"""Mod declaration contracts: what a mod needs from the platform.

A mod declares its channel groups, channel categories, roles, workflows
and commands in its YAML config (``config/mods/<mod>.yaml``); the core
provisions them automatically. Mods never extend core enums and never
hardcode platform IDs.

Reference: kingdoms-services#26, ADR-0003 (channel categories);
kingdoms-services#175 (channel groups, kinds and admin-only flags as
data — the single source of truth for a mod's salons).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHANNEL_KINDS = ("text", "forum", "announce")
"""The channel kinds a mod may declare (``announce`` = admin-written,
everyone-readable)."""


@dataclass(frozen=True, slots=True)
class ChannelAccessPolicy:
    """A category's declared audience (kingdoms-services#57).

    The declaration is the source of truth; the platform overwrites are
    the runtime state, compared by the ChannelAuditService (drift).
    """

    view: tuple[str, ...] = ()
    post: tuple[str, ...] = ()
    everyone_view: bool = True
    everyone_post: bool = False
    bot_overwrite: bool = True

    def to_dict(self) -> dict[str, object]:
        """Serialize for persistence and the platform seam."""
        return {
            "view": [str(v) for v in self.view],
            "post": [str(v) for v in self.post],
            "everyone_view": self.everyone_view,
            "everyone_post": self.everyone_post,
            "bot_overwrite": self.bot_overwrite,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChannelAccessPolicy:
        """Rebuild a policy from its persisted form."""
        return cls(
            view=tuple(str(v) for v in (data.get("view") or ())),
            post=tuple(str(v) for v in (data.get("post") or ())),
            everyone_view=bool(data.get("everyone_view", True)),
            everyone_post=bool(data.get("everyone_post", False)),
            bot_overwrite=bool(data.get("bot_overwrite", True)),
        )


@dataclass(frozen=True, slots=True)
class ChannelGroupDef:
    """A channel group (a Discord category) declared by a mod.

    Addressed at runtime as ``mod:<key>``; channels declare membership
    with their ``group`` key. The declaration order is the display order.
    """

    key: str
    display_name: str
    admin_only: bool = False
    position: int = 0


@dataclass(frozen=True, slots=True)
class ChannelCategoryDef:
    """A channel category declared by a mod.

    Addressed at runtime as ``mod:key`` (e.g. ``ladder:ladder_rankings``).
    """

    key: str
    display_name: str
    description: str = ""
    per_instance: bool = False
    group: str = ""
    kind: str = "text"
    admin_only: bool = False
    position: int = 0
    adopt: str = "name"
    access: ChannelAccessPolicy = ChannelAccessPolicy()


@dataclass(frozen=True, slots=True)
class RoleDef:
    """A role declared by a mod, referenced by logical role key."""

    key: str
    display_name: str
    color: int = 0x99AAB5
    hoisted: bool = False
    description: str = ""
    per_season: bool = False


@dataclass(frozen=True, slots=True)
class ModDefinition:
    """A mod's complete declaration: the core provisions from this only."""

    name: str
    enabled: bool = True
    seasonal: bool | None = None
    channel_categories: tuple[ChannelCategoryDef, ...] = field(default_factory=tuple)
    roles: tuple[RoleDef, ...] = field(default_factory=tuple)
    channel_groups: tuple[ChannelGroupDef, ...] = field(default_factory=tuple)
    workflows: tuple[str, ...] = field(default_factory=tuple)
    commands: tuple[str, ...] = field(default_factory=tuple)
    dependencies: tuple[str, ...] = field(default_factory=tuple)

    def require_seasonal(self) -> bool:
        """Return the declared mod type; fail loudly when undeclared.

        The mod type (seasonal or permanent) is **mandatory** at
        declaration time: the admin-surface machinery branches on it
        (a lifecycle panel + per-season salon for a seasonal mod, a
        guild-level config panel for a permanent one).
        """
        if self.seasonal is None:
            raise ValueError(
                f"Mod '{self.name}' must declare 'seasonal: true|false' in config/mods/" + self.name + ".yaml"
            )
        return self.seasonal

    def channel_category(self, key: str) -> ChannelCategoryDef:
        """Return the declared channel category for a key, fail loudly."""
        for cat in self.channel_categories:
            if cat.key == key:
                return cat
        raise KeyError(
            f"Mod '{self.name}' does not declare channel category '{key}'. Add it to config/mods/" + self.name + ".yaml"
        )

    def channel_group(self, key: str) -> ChannelGroupDef:
        """Return the declared channel group for a key, fail loudly."""
        for group in self.channel_groups:
            if group.key == key:
                return group
        raise KeyError(
            f"Mod '{self.name}' does not declare channel group '{key}'. Add it to config/mods/" + self.name + ".yaml"
        )

    def channels_of_group(self, group_key: str) -> tuple[ChannelCategoryDef, ...]:
        """Return the channels declared inside one group, in order."""
        return tuple(cat for cat in self.channel_categories if cat.group == group_key)

    def role(self, key: str) -> RoleDef:
        """Return the declared role for a key, fail loudly."""
        for role in self.roles:
            if role.key == key:
                return role
        raise KeyError(f"Mod '{self.name}' does not declare role '{key}'. Add it to config/mods/" + self.name + ".yaml")
