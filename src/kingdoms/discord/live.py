"""The /live dashboard command and its dedicated channel (#147).

The live test dashboard: a dedicated ``live-dashboard`` channel hosts one
persistent message showing every linked player's current state
(offline / in lobby / in game, match ref, since when). The message is
edited in place on every update — no spam. ``/live`` renders the same
dashboard ephemerally; ``/live-link`` and ``/live-unlink`` manage the
minimal profile links needed to test the providers (test-scoped, kept
simple per #147).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized

logger = logging.getLogger("kingdoms.live")

LIVE_CHANNEL_NAME = "live-dashboard"
LIVE_MESSAGE_KEY = "live-dashboard"

_STATE_ICONS = {"offline": "⚫", "in_lobby": "🟡", "in_game": "🟢"}


def render_dashboard(snapshot: dict[str, Any], locale: str = "en") -> str:
    """Render the dashboard snapshot as a plain-text message body."""
    lines: list[str] = []
    if snapshot.get("degraded"):
        lines.append("⚠️ Providers unreachable — states may be stale, all shown offline.")
    players = snapshot.get("players", [])
    if not players:
        lines.append("No linked players yet — link a profile with /live-link.")
    for p in players:
        icon = _STATE_ICONS.get(p["state"], "⚫")
        since = f" (since <t:{p['since'] // 1000}:R>)" if p.get("since") else ""
        ref = f" — match `{p['match_ref']}`" if p.get("match_ref") else ""
        lines.append(f"{icon} <@{p['user_id']}> — {p['state']}{ref}{since}")
    return "\n".join(lines)


def register_live_commands(
    tree: app_commands.CommandTree[discord.Client],
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /live, /live-link and /live-unlink commands.

    The commands read svc-core's Live service through ``LiveClient``
    when ``CORE_URI`` is set; without it (local runs, providers down),
    the dashboard degrades to a note — the command still answers.
    """
    from kingdoms.core.rpc.live import LiveClient

    core_uri = os.environ.get("CORE_URI", "")
    client = LiveClient(core_uri) if core_uri else None

    def _render_locale(catalog: MessageCatalog | None, locale: str, key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        return catalog.render(key, locale)

    @tree.command(
        name=localized("commands.live_name", "live"),
        description=localized("commands.live_description", "Live test dashboard: linked players, lobbies and games"),
    )
    async def live_command(interaction: discord.Interaction) -> None:
        """Answer /live with the current dashboard snapshot."""
        await interaction.response.defer(ephemeral=True)
        if client is None:
            await interaction.followup.send("Live dashboard unavailable: CORE_URI not configured.", ephemeral=True)
            return
        try:
            snapshot = await client.watch("aoe2")
        except Exception:
            logger.warning("live snapshot fetch failed", exc_info=True)
            snapshot = {"players": [], "generated_at": 0, "degraded": True}
        body = render_dashboard(snapshot, _locale(interaction))
        await interaction.followup.send(body, ephemeral=True)

    @tree.command(
        name=localized("commands.live_link_name", "live-link"),
        description=localized(
            "commands.live_link_description", "Link your AoE2 profile id to your account (test scope)"
        ),
    )
    @app_commands.describe(profile_id="Your AoE2 profile id")
    async def live_link_command(interaction: discord.Interaction, profile_id: str) -> None:
        """Link the invoker's AoE2 profile (minimal test-scoped link)."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        if interaction.user.id is None or profile_id.strip() == "":
            await interaction.response.send_message("A profile id is required.", ephemeral=True)
            return
        binding = {
            "_id": f"binding:aoe2:{interaction.user.id}",
            "user_id": str(interaction.user.id),
            "game_key": "aoe2",
            "profile_id": profile_id.strip(),
            "bound_at": int(_now_ms()),
        }
        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        await db.replace_one({"_id": binding["_id"]}, binding, upsert=True)
        await interaction.response.send_message(
            f"Linked profile `{profile_id.strip()}` to <@{interaction.user.id}> — the dashboard will show it.",
            ephemeral=True,
        )

    @tree.command(
        name=localized("commands.live_unlink_name", "live-unlink"),
        description=localized("commands.live_unlink_description", "Unlink your AoE2 profile from your account"),
    )
    async def live_unlink_command(interaction: discord.Interaction) -> None:
        """Remove the invoker's AoE2 profile link."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        await db.delete_one({"_id": f"binding:aoe2:{interaction.user.id}"})
        await interaction.response.send_message("Profile link removed.", ephemeral=True)


def _locale(interaction: discord.Interaction) -> str:
    """Best-effort locale: the user's Discord locale, defaulting to en."""
    return str(interaction.locale) if interaction.locale else "en"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


async def ensure_live_dashboard_channel(guild: discord.Guild) -> discord.TextChannel:
    """Resolve or create the guild's live-dashboard channel (idempotent)."""
    for channel in guild.text_channels:
        if channel.name == LIVE_CHANNEL_NAME:
            return channel
    return await guild.create_text_channel(LIVE_CHANNEL_NAME, reason="Kingdoms live test dashboard (#147)")
