"""The bot-admin DM panel: cross-guild platform actions (core).

A bot admin is cross-guild: the platform-wide actions live in their DMs,
never in one guild's pinned panel, and are visible/clickable by nobody
else. This panel reprises the existing bot-admin surfaces and adds the
per-guild activation seam:

- **Access requests** — every registered guild requests access to
  games/mods here (default: nothing active); the bot admin approves or
  denies, and can revoke a granted key at any time.
- **Content refresh** — re-run the vendored dataset's extraction into
  the catalog + per-locale content store (game updates: new civs/maps,
  changed unique units/tech trees).

The DM locale of the invoking admin is reused for rendering (user
settings, ``logs_service.get_user_locale``). The namespace
``admin:dm:`` is disjoint from the guild panels' namespaces, so a DM
click has exactly one dispatch path.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.admin_dm")

_NS = "admin:dm"


def _is_bot_admin(interaction: discord.Interaction) -> bool:
    """Whether the clicker is a BOT_ADMINS operator (the DM panel's guard)."""
    admins = getattr(getattr(interaction.client, "status_service", None), "bot_admins", ())
    return str(getattr(interaction.user, "id", "")) in tuple(admins)


async def _deny(interaction: discord.Interaction) -> None:
    """Answer a non-bot-admin click ephemerally and mark it handled."""
    try:
        await interaction.response.send_message("Réservé aux bot admins.", ephemeral=True)
    except Exception:
        logger.warning("DM PANEL denial answer failed — best-effort", exc_info=True)


async def _access_service() -> Any | None:
    from kingdoms.discord.wiring import build_guild_access_service

    return build_guild_access_service()


async def build_admin_dm_panel(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Render the bot-admin DM panel: pending requests, grants, refresh."""
    service = await _access_service()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# 🛡️ Panneau bot admin (cross-guild)")]
    if service is None:
        blocks.append(discord.ui.TextDisplay("Wiring indisponible (Mongo absent)."))
        view.add_item(discord.ui.Container(*blocks))
        return view
    requests = await service.pending_requests()
    if requests:
        lines = []
        for request in requests[:20]:
            guild_id = request.get("guild_id")
            keys = ", ".join(request.get("keys") or [])
            lines.append(f"- Guilde `{guild_id}` : {keys}")
        blocks.append(discord.ui.TextDisplay("## Demandes d'accès en attente\n" + "\n".join(lines)))
    else:
        blocks.append(discord.ui.TextDisplay("_Aucune demande d'accès en attente._"))
    access_docs = await service.list_all()
    granted_lines = []
    for doc in access_docs[:20]:
        games = ", ".join(doc.get("games") or []) or "—"
        mods = ", ".join(doc.get("mods") or []) or "—"
        granted_lines.append(f"- Guilde `{doc.get('guild_id')}` : games={games}, mods={mods}")
    if granted_lines:
        blocks.append(discord.ui.TextDisplay("## Accords actifs\n" + "\n".join(granted_lines)))
    view.add_item(discord.ui.Container(*blocks))
    if requests:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        first = requests[0]
        row.add_item(
            AccessApproveButton(str(first["guild_id"]), str(first["requested_at"]), len(requests))
        )
        row.add_item(AccessDenyButton(str(first["guild_id"]), str(first["requested_at"])))
        view.add_item(row)
    refresh_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    refresh_row.add_item(ContentRefreshButton())
    view.add_item(refresh_row)
    return view


class AccessApproveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:approve:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Approve the guild's pending access request (bot admins only)."""

    def __init__(self, guild_id: str, requested_at: str, pending: int = 1) -> None:
        self.guild_id = guild_id
        self.requested_at = requested_at
        super().__init__(
            discord.ui.Button(
                label=f"Approuver ({pending} en attente)" if pending > 1 else "Approuver",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:approve:{guild_id}:{requested_at}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessApproveButton:
        """Rebuild from the wire (guild + request timestamps)."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, approve, re-render the panel."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        service = await _access_service()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        try:
            await service.approve(self.guild_id, self.requested_at)
        except Exception:
            logger.exception("DM PANEL: approve failed (guild %s)", self.guild_id)
            await interaction.response.send_message("Approbation échouée (déjà traitée ?).", ephemeral=True)
            return
        await interaction.response.edit_message(view=await build_admin_dm_panel(interaction))
        await interaction.followup.send(
            f"Accès accordé à la guilde `{self.guild_id}`.", ephemeral=True
        )


class AccessDenyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:deny:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Deny the guild's pending access request (bot admins only)."""

    def __init__(self, guild_id: str, requested_at: str) -> None:
        self.guild_id = guild_id
        self.requested_at = requested_at
        super().__init__(
            discord.ui.Button(
                label="Refuser",
                style=discord.ButtonStyle.danger,
                custom_id=f"{_NS}:deny:{guild_id}:{requested_at}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> AccessDenyButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, deny, re-render the panel."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        service = await _access_service()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        try:
            await service.deny(self.guild_id, self.requested_at)
        except Exception:
            logger.exception("DM PANEL: deny failed (guild %s)", self.guild_id)
            await interaction.response.send_message("Refus échoué (déjà traitée ?).", ephemeral=True)
            return
        await interaction.response.edit_message(view=await build_admin_dm_panel(interaction))


class ContentRefreshButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:refresh",
):
    """Refresh the localized faction content from the vendored dataset."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rafraîchir le contenu civs/maps",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:refresh",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ContentRefreshButton:
        """Rebuild from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard, run the dataset refresh (catalog + content), confirm."""
        if not _is_bot_admin(interaction):
            await _deny(interaction)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content

            counts = await refresh_aoe2_content()
        except Exception:
            logger.exception("DM PANEL: content refresh failed")
            await interaction.followup.send("Refresh échoué (voir les logs).", ephemeral=True)
            return
        await interaction.followup.send(
            f"Contenu rafraîchi : {counts.get('factions', 0)} factions, "
            f"{counts.get('content_docs', 0)} documents de contenu.",
            ephemeral=True,
        )


def register_admin_dm_items(bot: discord.Client) -> None:
    """Register the DM panel's persistent dynamic items."""
    bot.add_dynamic_items(AccessApproveButton)
    bot.add_dynamic_items(AccessDenyButton)
    bot.add_dynamic_items(ContentRefreshButton)
