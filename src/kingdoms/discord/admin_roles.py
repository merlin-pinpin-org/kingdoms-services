"""The admin panel's roles view: core roles + mod-declared roles, as info.

The guild's role surface has two layers, both shown here (displayed
even when no action is available — the info is the point):

- the **core roles** the platform provisions (bot-admins);
- every enabled mod's declared roles (its YAML ``roles:``), with their
  resolved platform role (mention) when provisioned.

The view reads the running bot's role services; mods surface through
the shared ModRegistry — no per-mod wiring. Rebinding a logical role
key to an existing platform role stays in the roles service (the admin
panel only displays the state here; the rebind flow is a follow-up).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin_roles")


async def build_roles_view(interaction: discord.Interaction, wiring: Any) -> discord.ui.LayoutView:
    """Render the roles view: core + mod roles, mention + state each."""
    client = getattr(interaction, "client", None)
    guild = getattr(interaction, "guild", None)
    registry = getattr(client, "registry", None)
    mod_roles = getattr(client, "mod_roles_service", None)
    guild_id = str(getattr(interaction, "guild_id", "") or "")

    lines: list[str] = ["## 🛡 Rôles"]
    bot_admins = _bot_admins_role(guild)
    if bot_admins is not None:
        lines.append(f"- **bot-admins** — {bot_admins.mention} (rôle cœur, administrateurs du bot)")
    else:
        lines.append("- **bot-admins** — pas encore provisionné")

    lines.append("")
    lines.append("## 🧩 Rôles des mods")
    mod_lines: list[str] = []
    if registry is not None:
        for name, definition in registry.enabled().items():
            for role_def in definition.roles:
                mention = ""
                if mod_roles is not None and guild_id:
                    try:
                        role_id = await mod_roles.resolve_role_id(guild_id, name, role_def.key)
                        mention = f"<@&{role_id}>" if role_id else "_non provisionné_"
                    except Exception:
                        mention = "_?_"
                mod_lines.append(f"- **{name}:{role_def.key}** — {role_def.display_name} — {mention}")
    if mod_lines:
        lines.extend(mod_lines)
    else:
        lines.append("_Aucun rôle déclaré par les mods._")

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines))))
    return view


def _bot_admins_role(guild: Any) -> Any | None:
    if guild is None:
        return None
    for role in getattr(guild, "roles", ()):
        if role.name == "bot-admins":
            return role
    return None
