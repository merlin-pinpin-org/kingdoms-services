"""The mod menus' STANDARD content (botforge-services#378, #379).

Every mod's home and admin surfaces render their declaration's
presentation — a mod gets its surfaces FOR FREE (the pattern is
binding, documented on #378/#379):

- **The home** (`menu:mod:<name>`): the mod's type (seasonal or
  permanent) and the declared commands — the player sees what the mod
  is. The installed state never rides the blocks: a mod menu renders
  in the mod's own channel, the install is obvious (owner direction
  2026-11-13).
- **The admin** (`menu:mod:<name>:admin`): the same identity plus the
  declared workflows — the operator sees what they operate. The season
  lifecycle panel and the candidates join here with the portage (P3).

The blocks are DATA rendered in the guild's locale; the declared
buttons (the admin flip, the mod's own actions) stay the declared
views' business. The norm: no hardcoded ids, no discord imports in the
mod's service — this is the PLATFORM's gift to every mod.
"""

from __future__ import annotations

import re
from typing import Any

_MOD_HOME_RE = re.compile(r"^menu:mod:(?P<name>[a-z0-9_-]+)$")
_MOD_ADMIN_RE = re.compile(r"^menu:mod:(?P<name>[a-z0-9_-]+):admin$")


async def mod_standard_blocks(
    bot: Any,
    guild_id: str,
    view_id: str,
    tr: Any,
) -> list[Any]:
    """Build the standard blocks of one mod menu (empty for non-mod views).

    ``tr`` renders a locale key in the guild's language (the
    scope_pins ``_tr`` shape: key, locale, fallback).
    """
    import discord

    home = _MOD_HOME_RE.match(view_id)
    admin = _MOD_ADMIN_RE.match(view_id)
    if not home and not admin:
        return []
    name = (home or admin)["name"]  # type: ignore[index]
    registry = getattr(bot, "registry", None)
    definition = registry.get(name) if registry is not None else None
    if definition is None:
        return []
    locale = await _locale_of(bot, guild_id)

    def _tr(key: str, fallback: str) -> str:
        rendered: str = tr(_catalog(bot), key, locale, fallback)
        return rendered

    # the mod's TYPE rides BOTH surfaces (owner direction 2026-11-13):
    # the installed state was obvious (the channel would not exist
    # otherwise) — the type is the identity, home and admin carry it
    seasonal = bool(getattr(definition, "seasonal", False))
    blocks: list[Any] = [
        discord.ui.TextDisplay(
            "📅 "
            + _tr(
                "mods.standard.type_seasonal" if seasonal else "mods.standard.type_permanent",
                "Seasonal mod" if seasonal else "Permanent mod",
            )
        )
    ]
    # the declared roles no longer ride the standard blocks: the view's
    # notice carries them as MENTIONS with their use, plus the bot admins
    # (owner direction 2026-11-13 — one pattern everywhere).
    commands = tuple(getattr(definition, "commands", ()) or ())
    if commands:
        blocks.append(
            discord.ui.TextDisplay(
                f"⌨️ {_tr('mods.standard.commands', 'Commands')}: {', '.join(commands)}"
            )
        )
    if admin:
        workflows = tuple(getattr(definition, "workflows", ()) or ())
        if workflows:
            blocks.append(
                discord.ui.TextDisplay(
                    f"⚙️ {_tr('mods.standard.workflows', 'Workflows')}: {', '.join(workflows)}"
                )
            )
    return blocks


async def _locale_of(bot: Any, guild_id: str) -> str:
    """Return the guild's locale (en fallback)."""
    from botforge.discord.scope_pins import _locale_of

    return await _locale_of(bot, guild_id)


def _catalog(bot: Any) -> Any:
    """Return the message catalog (None-safe)."""
    from botforge.discord.scope_pins import _catalog

    return _catalog(bot)


__all__ = ["mod_standard_blocks"]
