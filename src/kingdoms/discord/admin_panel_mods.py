"""Runtime extension seam for the pinned admin panel (kingdoms-services#177).

The pinned bot-admins panel is the platform's **single admin surface**
(CONVENTIONS.md, *Guild-level settings and the admin surface*): a mod
never builds its own admin panel or re-declares core guild settings
(locale, reference timezone, managed channels). Instead, every enabled
mod registers its admin section here at startup — a stable key, a
localized label and a route — and the pinned main menu renders the
registered sections dynamically: present when the mod is enabled,
absent when it is not, with no core code change per mod.

Namespace discipline (the ADR lesson of this module's sibling): one
custom_id is served by exactly one dispatch mechanism. Mod sections
ride their own ``admin:pin:mod:<mod>:`` namespace — never shared with
live-view closures or the core ids — so a click has exactly one
dispatch path, restart-proof, with no captured state.

The seam deliberately exposes no locale, timezone or channel widgets:
core guild settings stay owned by the core panel. A mod's genuinely
mod-specific settings live in its YAML ``settings:`` (data), surfaced
through its own registered section.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin_panel_mods")

__all__ = [
    "AdminModSection",
    "ModSectionError",
    "mod_section_route_id",
    "register_admin_mod_section",
    "registered_admin_mod_sections",
    "unregister_admin_mod_section",
]

_MOD_ROUTE_PREFIX = "admin:pin:mod"


class ModSectionError(Exception):
    """Raised on an invalid mod-section registration (fail loudly, never silent)."""


@dataclass(frozen=True)
class AdminModSection:
    """One mod's admin section declaration.

    - ``mod`` — the mod's stable key (its YAML id; e.g. ``kingdoms``);
    - ``label`` — the section label, already localized by the mod
      (the mod reads the shared catalog through its own context);
    - ``entry`` — the async callback building the section's view
      (a Components V2 LayoutView), invoked with the interaction;
      click-time guards are the callback's responsibility (the seam
      serves admins only, see the handler below);
    - ``description`` — optional one-line section description.
    """

    mod: str
    label: str
    entry: Callable[..., Coroutine[Any, Any, discord.ui.LayoutView]]
    description: str = ""
    core: bool = False

    def __post_init__(self) -> None:
        """Fail loudly on a declaration that could never reach the wire."""
        if not self.mod or ":" in self.mod or not re.fullmatch(r"[a-z0-9_-]+", self.mod):
            raise ModSectionError(f"invalid mod key for an admin section: {self.mod!r}")
        if not self.label:
            raise ModSectionError("an admin section needs a non-empty label")


_REGISTRY: dict[str, AdminModSection] = {}
_ORDER: list[str] = []


def mod_section_route_id(mod: str) -> str:
    """Return the wire id of one mod's admin-section route select."""
    return f"{_MOD_ROUTE_PREFIX}:{mod}"[:100]


def register_admin_mod_section(section: AdminModSection) -> None:
    """Register a mod's admin section (idempotent per mod key).

    Re-registering the same key replaces the previous declaration —
    the startup wiring calls this once per enabled mod.
    """
    if section.mod in _REGISTRY and _REGISTRY[section.mod] is not section:
        logger.debug("ADMIN PANEL: re-registering mod section %s", section.mod)
    _REGISTRY[section.mod] = section
    if section.mod not in _ORDER:
        _ORDER.append(section.mod)


def unregister_admin_mod_section(mod: str) -> None:
    """Remove a mod's section (mod disabled or shutting down)."""
    _REGISTRY.pop(mod, None)
    if mod in _ORDER:
        _ORDER.remove(mod)


def registered_admin_mod_sections() -> tuple[AdminModSection, ...]:
    """Return the registered sections, in registration order."""
    return tuple(_REGISTRY[key] for key in _ORDER if key in _REGISTRY)


def registered_admin_core_sections() -> tuple[AdminModSection, ...]:
    """Return the registered core sections (games), in registration order."""
    return tuple(s for s in registered_admin_mod_sections() if s.core)


def registered_admin_game_sections() -> tuple[AdminModSection, ...]:
    """Return the registered mod sections (non-core), in registration order."""
    return tuple(s for s in registered_admin_mod_sections() if not s.core)


class PinModRouteSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:mod:(?P<mod>[a-z0-9_-]+)",
):
    """The mod-sections routing select of the pinned panel.

    One select listing every registered mod section; choosing one
    renders the mod's entry view. The options are rebuilt from the
    registry at click time, so enabling/disabling a mod needs no
    panel rebuild.
    """

    def __init__(self, options: list[discord.SelectOption], scope: str = "mods", placeholder: str = "") -> None:
        self.scope = scope
        super().__init__(
            discord.ui.Select(
                custom_id=mod_section_route_id(scope),
                options=options,
                placeholder=placeholder or None,
            )
        )

    @classmethod
    def create(cls, scope: str = "mods", placeholder: str = "") -> PinModRouteSelect:
        """Build the scoped select from the current registry (empty -> hidden)."""
        sections = (
            registered_admin_core_sections() if scope == "games" else registered_admin_game_sections()
        )
        options = [
            discord.SelectOption(label=section.label, value=section.mod, description=section.description or None)
            for section in sections
        ]
        return cls(options, scope, placeholder)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinModRouteSelect:
        """Rebuild the select from the wire (registry is read at click time)."""
        return cls.create()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Route to the chosen mod's section entry — admins only, at click time.

        The guard resolves through the panel wiring (BOT_ADMINS, guild
        admins, or the ``bot-admins`` role) — never a captured snapshot.
        """
        from kingdoms.discord.admin_persistent import _wiring
        from kingdoms.discord.guards import require_admin

        data = interaction.data
        values: Any = None
        if data is not None:
            raw = getattr(data, "values", None)
            if callable(raw):
                raw = None
            if raw is None and isinstance(data, dict):
                raw = data.get("values")
            if isinstance(raw, (list, tuple)):
                values = list(raw)
        chosen = (values or [""])[0]
        section = _REGISTRY.get(chosen)
        if section is None:
            await interaction.response.edit_message(
                content=f"Unknown admin section: `{chosen}`.",
                view=None,
            )
            return
        wiring = _wiring()
        bot_admins = tuple(getattr(wiring, "bot_admins", ()) or ()) if wiring is not None else ()
        roles_service = getattr(wiring, "roles_service", None) if wiring is not None else None
        if not await require_admin(interaction, bot_admins, roles_service):
            return
        try:
            view = await section.entry(interaction)
        except Exception:
            logger.exception("ADMIN PANEL: mod section %s failed to render", section.mod)
            message = "The admin section failed to render."
            if not interaction.response.is_done():
                await interaction.response.send_message(message, ephemeral=True)
            else:
                await interaction.followup.send(message, ephemeral=True)
            return
        if not interaction.response.is_done():
            await interaction.response.edit_message(view=view)
        else:
            await interaction.followup.send(view=view, ephemeral=True)
