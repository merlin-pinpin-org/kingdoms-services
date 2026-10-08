"""Guild access requests: the guild side of the activation seam (core).

Every registered guild starts with nothing active. A guild admin opens
/admin, picks what the guild needs, and the request lands in the
``guild_access`` store — a bot admin then approves or denies it from
the DM panel. Approval DMs are not needed on this side: the request
itself notifies the bot admins (best-effort DM, like the crash
reporter), so a request never waits on an admin browsing the panel.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.guild_access_request")

_NS = "admin:access"


async def _access_service() -> Any | None:
    from kingdoms.discord.wiring import build_guild_access_service

    return build_guild_access_service()


class GuildAccessRequestSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=rf"{_NS}:select:(?P<guild_id>\d+)",
):
    """Pick the games/mods the guild requests (guild admins only)."""

    def __init__(self, guild_id: str, options: list[discord.SelectOption] | None = None) -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:select:{guild_id}"[:100],
                options=options or [discord.SelectOption(label="Indisponible", value="none")],
                placeholder="Games/mods a demander...",
                min_values=1,
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
    ) -> GuildAccessRequestSelect:
        """Rebuild the select's options at click time (known keys only)."""
        del interaction, item
        return cls(match.group("guild_id"), _request_options())

    async def callback(self, interaction: discord.Interaction) -> None:
        """Record the request, DM the bot admins, confirm."""
        service = await _access_service()
        if service is None:
            await interaction.response.send_message("Wiring indisponible.", ephemeral=True)
            return
        data: Any = interaction.data or {}
        data_values: Any = data.get("values") or []
        chosen = [str(v) for v in data_values if v and v != "none"]
        if not chosen:
            await interaction.response.defer()
            return
        try:
            doc = await service.request_access(self.guild_id, chosen)
        except Exception:
            logger.exception("ACCESS REQUEST failed (guild %s)", self.guild_id)
            await interaction.response.send_message(
                "Demande échouée (déjà demandé/accordé ? voir les logs).", ephemeral=True
            )
            return
        pending = dict(doc.get("pending") or {})
        requested_at = max(pending, key=int) if pending else ""
        await _notify_bot_admins(interaction, self.guild_id, chosen, requested_at)
        await interaction.response.send_message(
            "Demande enregistrée — un bot admin l'approuvera depuis ses DMs.", ephemeral=True
        )


class GuildAccessRequestButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<guild_id>\d+)",
):
    """Open the request picker from the guild's /admin panel."""

    def __init__(self, guild_id: str) -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label="Demander l'accès aux games/mods",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:open:{guild_id}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> GuildAccessRequestButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Guard (guild admins), then open the request select."""
        from kingdoms.discord.maps_pool_flow import _guard_admin

        if not await _guard_admin(interaction):
            return
        select = GuildAccessRequestSelect(self.guild_id, _request_options())
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        row.add_item(select)
        picker = discord.ui.LayoutView(timeout=None)
        picker.add_item(row)
        picker_view: Any = picker
        await interaction.response.send_message(
            "Choisis les games/mods que la guilde demande :",
            view=picker_view,
            ephemeral=True,
        )


def _request_options() -> list[discord.SelectOption]:
    """List the platform's known games and mods as request options."""
    from kingdoms.discord.wiring import guild_access_platform

    games, mods = guild_access_platform()
    options = [discord.SelectOption(label=f"game : {g}", value=f"game:{g}") for g in games]
    options += [discord.SelectOption(label=f"mod : {m}", value=f"mod:{m}") for m in mods]
    return options or [discord.SelectOption(label="Indisponible", value="none")]


_PENDING_DMS: dict[str, list[tuple[str, str]]] = {}


def _dm_key(guild_id: str, requested_at: str) -> str:
    return f"{guild_id}:{requested_at}"


async def _notify_bot_admins(
    interaction: discord.Interaction,
    guild_id: str,
    keys: list[str],
    requested_at: str,
) -> None:
    """DM every bot admin about the pending request (best-effort).

    The sent DM ids are tracked per request so any bot admin's answer
    (approve or deny) can delete the copies the other admins received —
    a request is handled exactly once and no stale DM stays behind.
    """
    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    guild_name = getattr(getattr(interaction, "guild", None), "name", guild_id)
    content = f"**Demande d'accès** — guilde {guild_name} (`{guild_id}`) : {', '.join(keys)}"
    sent: list[tuple[str, str]] = []
    for admin_id in admins:
        if not str(admin_id).strip().isdigit():
            continue
        try:
            user = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
            dm = await user.create_dm()
            message = await dm.send(content)
            sent.append((str(admin_id), str(message.id)))
        except Exception:
            logger.warning("ACCESS REQUEST admin DM failed (admin %s) — best-effort", admin_id)
    _PENDING_DMS[_dm_key(guild_id, requested_at)] = sent


async def _delete_pending_admin_dms(bot: Any, guild_id: str, requested_at: str) -> None:
    """Delete the request DMs every admin received once one admin answered."""
    sent = _PENDING_DMS.pop(_dm_key(guild_id, requested_at), None) or []
    for admin_id, message_id in sent:
        try:
            user = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
            dm = await user.create_dm()
            await dm.get_partial_message(int(message_id)).delete()
        except Exception:
            logger.debug(
                "ACCESS REQUEST admin DM cleanup skipped (admin %s, message %s)",
                admin_id,
                message_id,
                exc_info=True,
            )


def register_guild_access_request_items(bot: discord.Client) -> None:
    """Register the request surface's persistent dynamic items."""
    bot.add_dynamic_items(GuildAccessRequestSelect)
    bot.add_dynamic_items(GuildAccessRequestButton)
