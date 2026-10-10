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

    def __init__(
        self, guild_id: str, options: list[discord.SelectOption] | None = None, disabled: bool = False
    ) -> None:
        self.guild_id = guild_id
        resolved = options or [discord.SelectOption(label="Aucun game/mod actif", value="none")]
        super().__init__(
            discord.ui.Select(
                custom_id=f"{_NS}:select:{guild_id}"[:100],
                options=resolved,
                placeholder="Games/mods a demander...",
                min_values=1,
                max_values=min(25, len(resolved)),
                disabled=disabled or all(o.value == "none" for o in resolved),
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
        """Ack within the 3s window, then record the request and DM admins.

        The interaction is deferred (ephemeral) FIRST — Mongo writes and
        admin DMs can exceed Discord's 3-second ack window, which left
        the request "not responding". The confirmation lands as a followup.
        """
        data: Any = interaction.data or {}
        data_values: Any = data.get("values") or []
        chosen = [str(v) for v in data_values if v and v != "none"]
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not chosen:
            await interaction.followup.send("Rien a demander — aucun game/mod sélectionné.", ephemeral=True)
            return
        service = await _access_service()
        if service is None:
            await interaction.followup.send("Wiring indisponible.", ephemeral=True)
            return
        from kingdoms.core.services.guild_access import GuildAccessError

        try:
            doc = await service.request_access(self.guild_id, chosen)
        except GuildAccessError as error:
            await interaction.followup.send(f"Demande refusée : {error}", ephemeral=True)
            return
        except Exception:
            logger.exception("ACCESS REQUEST failed (guild %s)", self.guild_id)
            await interaction.followup.send("Demande échouée (voir les logs).", ephemeral=True)
            return
        pending = dict(doc.get("pending") or {})
        requested_at = max(pending, key=int) if pending else ""
        await interaction.followup.send(
            "Demande enregistrée — un bot admin l'approuvera depuis ses DMs.", ephemeral=True
        )
        await _notify_bot_admins(interaction, self.guild_id, chosen, requested_at)


class GuildAccessRequestButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:open:(?P<guild_id>\d+)",
):
    """Open the request picker from the guild's /admin panel."""

    def __init__(self, guild_id: str) -> None:
        self.guild_id = guild_id
        super().__init__(
            discord.ui.Button(
                label="Request access to games/mods",
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
        """Guard (guild admins), then open the request select.

        The guard's role lookups ride behind an immediate ack — the 3s
        window is not waited on the RolesService.
        """
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not await _guard_admin_deferred(interaction):
            return
        options = _request_options()
        if all(option.value == "none" for option in options):
            await interaction.followup.send(
                "Aucun game/mod actif sur la plateforme pour le moment — demande a un bot admin d'en activer un.",
                ephemeral=True,
            )
            return
        select = GuildAccessRequestSelect(self.guild_id, options)
        picker = discord.ui.View(timeout=None)
        picker.add_item(select)
        await interaction.followup.send(
            "Choisis les games/mods que la guilde demande :",
            view=picker,
            ephemeral=True,
        )


async def _guard_admin_deferred(interaction: discord.Interaction) -> bool:
    """Admin guard for an already-deferred interaction (followup denial)."""
    from kingdoms.discord.guards import is_admin

    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    roles = getattr(bot, "roles_service", None)
    if await is_admin(interaction, admins, roles):
        return True
    await interaction.followup.send("Réservé aux admins.", ephemeral=True)
    return False


def _request_options() -> list[discord.SelectOption]:
    """List the platform's known games and mods as request options."""
    from kingdoms.discord.wiring import guild_access_platform

    games, mods = guild_access_platform()
    options = [discord.SelectOption(label=f"game : {g}", value=f"game:{g}") for g in games]
    options += [discord.SelectOption(label=f"mod : {m}", value=f"mod:{m}") for m in mods]
    return options or [discord.SelectOption(label="Aucun game/mod actif", value="none")]


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
    view = discord.ui.View(timeout=None)
    view.add_item(GuildAccessApproveButton(guild_id, requested_at))
    view.add_item(GuildAccessDenyButton(guild_id, requested_at))
    sent: list[tuple[str, str]] = []
    for admin_id in admins:
        if not str(admin_id).strip().isdigit():
            continue
        try:
            user = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
            dm = await user.create_dm()
            message = await dm.send(content, view=view)
            sent.append((str(admin_id), str(message.id)))
        except Exception:
            logger.warning("ACCESS REQUEST admin DM failed (admin %s) — best-effort", admin_id)
    _PENDING_DMS[_dm_key(guild_id, requested_at)] = sent


async def _guard_bot_admin_deferred(interaction: discord.Interaction) -> bool:
    """Bot-admin guard for an already-deferred interaction (followup denial)."""
    from kingdoms.discord.guards import is_bot_admin

    bot = interaction.client
    admins = getattr(getattr(bot, "status_service", None), "bot_admins", ())
    if is_bot_admin(interaction.user.id, admins):
        return True
    await interaction.followup.send("Réservé aux bot admins.", ephemeral=True)
    return False


async def _answer_pending_request(
    interaction: discord.Interaction,
    guild_id: str,
    requested_at: str,
    *,
    approve: bool,
) -> None:
    """Handle one admin's answer: mutate, clean every admin's DM copy, reply."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not await _guard_bot_admin_deferred(interaction):
        return
    service = await _access_service()
    if service is None:
        await interaction.followup.send("Wiring indisponible.", ephemeral=True)
        return
    try:
        if approve:
            doc = await service.approve(guild_id, requested_at)
        else:
            doc = await service.deny(guild_id, requested_at)
    except Exception:
        logger.warning("ACCESS REQUEST answer failed (guild %s, approve=%s)", guild_id, approve)
        await interaction.followup.send("Demande introuvable (déjà traitée ?).", ephemeral=True)
        return
    await _delete_pending_admin_dms(interaction.client, guild_id, requested_at)
    granted = ", ".join((doc.get("games") or []) + [f"mod:{m}" for m in doc.get("mods") or []])
    if approve:
        await _refresh_guild_pins(interaction.client, guild_id)
        await interaction.followup.send(
            f"Accès accordé à la guilde `{guild_id}` — actifs : {granted or 'aucun'}.", ephemeral=True
        )
    else:
        await interaction.followup.send(f"Demande de `{guild_id}` refusée.", ephemeral=True)


async def _refresh_guild_pins(client: discord.Client, guild_id: str) -> None:
    """Re-render the guild's static pins after an access change (best-effort).

    A grant changes what the pinned admin panel must show (the games
    section appears); the static-pin cycle edits the live pin in place
    — id stable, content current.
    """
    from kingdoms.discord.static_pins import ensure_static_pin, registered_static_pins

    for spec in registered_static_pins():
        if spec.key != "admin-panel":
            continue
        try:
            await ensure_static_pin(client, spec, guild_id)
        except Exception:
            logger.warning("ACCESS REQUEST: admin pin refresh failed — best-effort", exc_info=True)


class GuildAccessApproveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:approve:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Bot-admin DM button: approve one guild's pending request."""

    def __init__(self, guild_id: str, requested_at: str) -> None:
        self.guild_id = guild_id
        self.requested_at = requested_at
        super().__init__(
            discord.ui.Button(
                label="Approuver",
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
    ) -> GuildAccessApproveButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Approve the request and clean the other admins' DM copies."""
        await _answer_pending_request(interaction, self.guild_id, self.requested_at, approve=True)


class GuildAccessDenyButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:deny:(?P<guild_id>\d+):(?P<requested_at>\d+)",
):
    """Bot-admin DM button: deny one guild's pending request."""

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
    ) -> GuildAccessDenyButton:
        """Rebuild from the wire."""
        del interaction, item
        return cls(match.group("guild_id"), match.group("requested_at"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Deny the request and clean the other admins' DM copies."""
        await _answer_pending_request(interaction, self.guild_id, self.requested_at, approve=False)


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
    bot.add_dynamic_items(GuildAccessApproveButton)
    bot.add_dynamic_items(GuildAccessDenyButton)
