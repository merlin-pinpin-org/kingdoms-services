"""The ladder mod's admin section, registered into the /admin panel (#133).

The section renders the ladder's admin surface in the panel's Mods
select: the current ladder (name, game, rating system, queue depth),
its settings snapshot, and the admin actions wired to the domain
services — settings update, rating adjustment, rating reset, match
cancellation (with compensation). Every action goes through
``LadderAdminService``/``AdminToolsService`` (validated + audited,
§9.5/§9.6); the section only renders and collects input — the click
guards stay the panel's (admins only, at click time).
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.discord.admin_panel_mods import AdminModSection, register_admin_mod_section
from kingdoms.discord.ladder_commands import build_ladder_wiring

logger = logging.getLogger("kingdoms.ladder.admin_panel")

MOD_KEY = "ladder"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


async def ladder_admin_entry(interaction: discord.Interaction) -> discord.ui.LayoutView:
    """Render the ladder admin section's view (read-only snapshot first)."""
    wiring = build_ladder_wiring()
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[Any] = [discord.ui.TextDisplay("# 🏆 Ladder admin")]
    if wiring is None:
        blocks.append(discord.ui.TextDisplay("Ladder wiring unavailable: Mongo/Redis not configured on this process."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        return view

    service = wiring.service
    guild_ref = str(interaction.guild_id) if interaction.guild_id else "guild:default"
    ladder = None
    for owner in (f"guild:{interaction.guild_id}", guild_ref, "guild:default", "default"):
        found = await service._db.find_ladder_by_owner(owner, "aoe2")
        if found is not None:
            ladder = found
            break
    if ladder is None:
        blocks.append(discord.ui.TextDisplay("No ladder exists for this guild yet — seed one first."))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        return view

    settings = ladder.get("settings", {})
    players = await service._db.find_ladder_players(str(ladder["_id"]))
    in_queue = sum(1 for p in players if p.get("in_queue"))
    blocks.extend(
        [
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"**{ladder.get('name', '?')}** — game `{ladder.get('game_key', 'aoe2')}`"),
            discord.ui.TextDisplay(
                f"Rating system: `{settings.get('rating_system', 'elo')}` · "
                f"Players: {len(players)} · In queue: {in_queue}"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "### Settings\n"
                f"- base ELO: {settings.get('base_elo', '?')} · initial: {settings.get('elo_initial', '?')} · "
                f"floor: {settings.get('elo_floor', '?')}\n"
                f"- K standard/newbie: {settings.get('elo_k_standard', '?')}/{settings.get('elo_k_newbie', '?')}\n"
                f"- rating threshold: {settings.get('base_elo_threshold', '?')} "
                f"(max {settings.get('elo_threshold_max', '?')})"
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "_Actions run through the audited admin services_ "
                "(`update_settings`, `adjust_rating`, `reset_ratings`, `cancel_match_with_compensation`) "
                "— use the modals from the ladder commands."
            ),
        ]
    )
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
    return view


def register_ladder_admin_section() -> None:
    """Register the ladder section into the /admin panel (idempotent)."""
    register_admin_mod_section(
        AdminModSection(
            mod=MOD_KEY,
            label="Ladder",
            description="Ladder settings, ratings and matches",
            entry=ladder_admin_entry,
        )
    )
