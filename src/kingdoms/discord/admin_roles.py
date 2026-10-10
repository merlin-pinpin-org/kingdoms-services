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
import re
from typing import Any

import discord

from kingdoms.discord.commands_i18n import tr

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
                suffix = " (un par saison, provisionné à la création de la saison)" if role_def.per_season else ""
                mod_lines.append(f"- **{name}:{role_def.key}** — {role_def.display_name} — {mention}{suffix}")
            if mod_roles is not None and guild_id:
                season_lines = await _provisioned_mod_roles(mod_roles, guild_id, name, definition)
                mod_lines.extend(season_lines)
    if mod_lines:
        lines.extend(mod_lines)
    else:
        lines.append("_Aucun rôle déclaré par les mods._")

    return await _roles_view_footer(lines, interaction)


async def _roles_view_footer(
    lines: list[str], interaction: discord.Interaction
) -> discord.ui.LayoutView:
    """Finish the roles view: function-mapping selects and the recreate button."""
    lines.append("")
    lines.append("## \U0001f511 Functions and roles")
    lines.append(
        "Map any guild role onto a bot function (bot-admins, staff) \u2014 "
        "BOT_ADMINS and guild administrators always pass; the mapping adds on top."
    )
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay("\n".join(lines))))
    from kingdoms.core.services.role_grants import FUNCTIONS

    mapping_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for function in FUNCTIONS:
        mapping_row.add_item(FunctionRolesSelect(function))
    view.add_item(mapping_row)
    from kingdoms.discord.wiring import guard_admin

    if await guard_admin(interaction):
        action_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        action_row.add_item(RecreateChannelsButton("all"))
        view.add_item(action_row)
    return view


async def _provisioned_mod_roles(mod_roles: Any, guild_id: str, name: str, definition: Any) -> list[str]:
    """List the mod's runtime-provisioned roles (per-season keys included)."""
    declared = {r.key for r in definition.roles}
    out: list[str] = []
    try:
        mappings = await mod_roles.list_role_mappings(guild_id, name)
    except Exception:
        return out
    for mapping in sorted(mappings, key=lambda m: m.role_key):
        if mapping.role_key in declared:
            continue
        mention = f"<@&{mapping.role_id}>" if mapping.role_id else "_cassé_"
        out.append(f"  - {name}:{mapping.role_key} — provisionné — {mention}")
    return out


def _bot_admins_role(guild: Any) -> Any | None:
    if guild is None:
        return None
    for role in getattr(guild, "roles", ()):
        if role.name == "bot-admins":
            return role
    return None


_ROLES_NS = "admin:roles"
_FUNCTIONS_NS = "admin:functions"


class FunctionRolesSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_FUNCTIONS_NS}:map:(?P<function>[a-z0-9_-]+)",
):
    """Map any guild role onto a bot function (bot-admins, staff...).

    A multi-select of the guild's roles; the chosen roles replace the
    function's mapping (empty selection resets to the default
    provisioned role). BOT_ADMINS and guild administrators always
    pass — the mapping adds on top, it never removes the baseline.
    """

    def __init__(self, function: str, options: list[discord.SelectOption] | None = None) -> None:
        self.function = function
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_FUNCTIONS_NS}:map:{function}"[:100],
                options=options or [discord.SelectOption(label="No role", value="none")],
                placeholder=f"Roles for {function}...",
                min_values=0,
                max_values=25,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> FunctionRolesSelect:
        """Rebuild the select at click time (the guild's roles, current mapping marked)."""
        import re as _re

        del item, _re
        function = match.group("function")
        guild = getattr(interaction, "guild", None)
        options = []
        if guild is not None:
            grants = getattr(interaction.client, "role_grants_service", None)
            current: tuple[str, ...] = ()
            guild_id = str(interaction.guild_id) if interaction.guild_id else ""
            if grants is not None and guild_id:
                try:
                    current = await grants.function_roles(guild_id, function)
                except Exception:
                    current = ()
            for role in sorted(guild.roles, key=lambda r: r.position, reverse=True)[1:26]:
                options.append(
                    discord.SelectOption(
                        label=role.name,
                        value=str(role.id),
                        default=str(role.id) in current,
                    )
                )
        return cls(function, options)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Persist the chosen roles as the function's mapping."""
        grants = getattr(interaction.client, "role_grants_service", None)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if grants is None or not guild_id:
            await interaction.response.send_message(
                await tr(interaction, "replies_shared.wiring_unavailable"), ephemeral=True
            )
            return
        chosen = tuple(self.item.values or ())
        if chosen == ("none",):
            chosen = ()
        try:
            await grants.set_function_roles(guild_id, self.function, chosen)
        except Exception:
            logger.warning("ROLE GRANTS mapping failed", exc_info=True)
            await interaction.response.send_message(
                await tr(interaction, "replies_shared.edit_failed"), ephemeral=True
            )
            return
        listed = ", ".join(f"<@&{r}>" for r in chosen) or "default"
        await interaction.response.send_message(
            f"`{self.function}` -> {listed}", ephemeral=True
        )


class RecreateChannelsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_ROLES_NS}:recreate:(?P<scope>all|home|admin|logs)",
):
    """Recreate a deleted managed channel now (guild admins).

    The event-driven healing already recreates on delete; this button
    forces a pass — the resolution re-checks existence and recreates
    anything missing (useful after a permissions mishap or a missed
    event).
    """

    def __init__(self, scope: str, label: str = "Recreate all channels") -> None:
        self.scope = scope
        super().__init__(
            discord.ui.Button(
                label=label,
                emoji="\U0001f527",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_ROLES_NS}:recreate:{scope}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> RecreateChannelsButton:
        """Rebuild the item from the wire."""
        del interaction, item
        return cls(match.group("scope"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Force the managed channels to exist (recreate the missing ones)."""
        bot = interaction.client
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        if not guild_id:
            await interaction.response.defer()
            return
        from kingdoms.discord.wiring import guard_admin

        if not await guard_admin(interaction):
            return
        recreated = await _recreate_channels(bot, guild_id, self.scope)
        message = (
            f"Recreated: {', '.join(recreated)}" if recreated else "Every managed channel already exists."
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def _recreate_channels(bot: Any, guild_id: str, scope: str) -> list[str]:
    """Resolve the managed channels, recreating the missing ones."""
    targets: dict[str, tuple[Any, tuple[str, ...]]] = {}
    admin_service = getattr(bot, "admin_channel_service", None)
    admin_ids = tuple(bot.status_service.bot_admins)
    if scope in ("all", "home"):
        targets["home"] = (getattr(bot, "home_channel_service", None), ())
    if scope in ("all", "admin"):
        targets["admin"] = (admin_service, admin_ids)
    if scope in ("all", "logs"):
        targets["logs"] = (getattr(bot, "logs_service", None), ())
    recreated: list[str] = []
    for name, (service, ids) in targets.items():
        if service is None:
            continue
        try:
            channel_id = (
                await service.resolve_channel(guild_id, ids) if ids else await service.resolve_channel(guild_id)
            )
            stored = getattr(service, "_db", None)
            before = None
            if stored is not None:
                existing = await stored.find_channel(guild_id, getattr(service, "_category", ""))
                before = existing.channel_id if existing is not None else None
            if channel_id and channel_id != before:
                recreated.append(name)
        except Exception:
            logger.warning("CHANNEL RECREATE failed (%s, guild %s)", name, guild_id, exc_info=True)
    return recreated


def register_roles_admin_items(bot: discord.Client) -> None:
    """Register the roles section's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(FunctionRolesSelect)
    bot.add_dynamic_items(RecreateChannelsButton)
