"""The providers admin panel (panel:game:aoe2:providers, ADR-0021).

The admin sub-menu of the aoe2 game admin: every provider configured
on the game service, with its last health-test results (the prober's
capabilities ping: ok, latency, consecutive failures). The data comes
from the game service — ``GetProviderTests`` on the GameData contract —
never from the providers directly (the bot never talks to them).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.providers_panel")

GAME_KEY = "aoe2"

_STATE_ICONS = {True: "🟢", False: "🔴"}


async def view_providers(interaction: discord.Interaction) -> None:
    """Render the providers panel (ephemeral, admin-guarded)."""
    from kingdoms.discord.wiring import guard_admin

    if not await guard_admin(interaction):
        return
    game_uri = os.environ.get("GAME_AOE2_URI", "")
    await interaction.response.defer(ephemeral=True)
    if not game_uri:
        await interaction.followup.send(
            "Panneau providers indisponible : GAME_AOE2_URI n'est pas configuré "
            "(le service game-aoe2 expose les tests).",
            ephemeral=True,
        )
        return
    try:
        from kingdoms.game_aoe2.client import GameDataClient

        client = GameDataClient(game_uri)
        results = await client.get_provider_tests(GAME_KEY)
    except Exception:
        logger.warning("providers panel fetch failed", exc_info=True)
        await interaction.followup.send(
            "Panneau providers indisponible : le service game-aoe2 ne répond pas.",
            ephemeral=True,
        )
        return
    await interaction.followup.send(
        embed=_render(results), ephemeral=True
    )


def _render(results: dict[str, Any]) -> discord.Embed:
    """Build the providers embed: one line per provider test."""
    tests = results.get("tests", [])
    from kingdoms.discord.ui.embeds import EmbedBuilder

    embed = EmbedBuilder.build(
        title=f"🔌 Providers — {GAME_KEY}",
        description=(
            "Santé des providers du service game-aoe2 "
            "(ping de capacités, rafraîchi chaque minute)."
        ),
        level="info",
    )
    if not tests:
        embed.add_field(
            name="Aucun test",
            value="Aucun provider configuré sur le service game-aoe2.",
            inline=False,
        )
        return embed
    for test in tests:
        ok = bool(test.get("ok", False))
        icon = _STATE_ICONS[ok]
        name = str(test.get("provider_id", "?"))
        latency = int(test.get("latency_ms", 0))
        failures = int(test.get("failures", 0))
        detail = str(test.get("detail", "") or "—")
        value = f"Latence : {latency} ms · Échecs consécutifs : {failures}\n`{detail}`"
        embed.add_field(name=f"{icon} {name}", value=value, inline=False)
    return embed
