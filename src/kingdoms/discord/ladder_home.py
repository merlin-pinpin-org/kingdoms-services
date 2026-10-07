"""The ladder's home view: the mod's front door inside the guild home.

The core home routes ``mod:ladder`` here; the mod decides what its
home shows. The ladder's home is **button-only** (the platform rule:
views, never commands to type): every action of the /ladder group has
its persistent button, served by DynamicItems in the
``ladder:home:`` namespace — one custom_id, one dispatch path,
restart-proof, click-time guards included.

Every action runs the same domain code as the slash commands
(`LadderSurface` / the core membership): the buttons are a second
entry point, never a second implementation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.discord.staff import StaffApplyButton

logger = logging.getLogger("kingdoms.ladder.home")

_NS = "ladder:home"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    import time

    return int(time.time() * 1000)


def _wiring_and_ladder_id(interaction: discord.Interaction) -> tuple[Any, str]:
    """Resolve the wiring and the guild's ladder id (empty when absent)."""
    from kingdoms.discord.ladder_commands import build_ladder_wiring

    wiring = build_ladder_wiring(bot=interaction.client)
    ladder_id = str(getattr(interaction.client, "_ladder_id", "") or "")
    return wiring, ladder_id


async def _run_membership(interaction: discord.Interaction, action: str) -> None:
    """Register or unregister the clicker through the core membership."""
    from kingdoms.mods.ladder.membership import build_ladder_membership

    wiring, ladder_id = _wiring_and_ladder_id(interaction)
    if wiring is None or not ladder_id:
        await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
        return
    membership = build_ladder_membership(wiring.service, ladder_id, wiring.season_service, wiring.season_roles)
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    user_id = str(interaction.user.id)
    if action == "register":
        result = await membership.register(guild_id, user_id, interaction.user.display_name)
    else:
        result = await membership.unregister(guild_id, user_id)
    await interaction.response.send_message(result.summary, ephemeral=True)


class LadderJoinButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:join",
):
    """Join the queue (same precondition and surface as /ladder join)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Rejoindre la file",
                emoji="⚔️",
                style=discord.ButtonStyle.success,
                custom_id=f"{_NS}:join",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderJoinButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the join flow (profile precondition, then queue)."""
        from kingdoms.discord.ladder_commands import _join_command

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        await _join_command(interaction, wiring.service, ladder_id)


class LadderLeaveButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:leave",
):
    """Leave the queue (same surface as /ladder leave)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Quitter la file",
                custom_id=f"{_NS}:leave",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderLeaveButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Leave the queue through the ladder surface."""
        from kingdoms.mods.ladder.surface import ACTION_LEAVE_QUEUE, LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        result = await surface.execute(
            ACTION_LEAVE_QUEUE, ladder_id, str(interaction.user.id), now=_now_ms()
        )
        if result.ok:
            await interaction.response.send_message("Tu as quitté la file.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Impossible : {result.reason}", ephemeral=True)


class LadderQueueButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:queue",
):
    """Show the current queue (same view as /ladder queue)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="File d'attente",
                custom_id=f"{_NS}:queue",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderQueueButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the current queue."""
        from kingdoms.mods.ladder.surface import LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        rows = await surface.queue_view(ladder_id, now=_now_ms())
        if not rows:
            body = "La file est vide."
        else:
            body = "\n".join(
                f"{i + 1}. <@{row.user_id}> — {row.rating} (en attente {row.wait_seconds // 60}m)"
                for i, row in enumerate(rows)
            )
        await interaction.response.send_message(body, ephemeral=True)


class LadderLeaderboardButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:leaderboard",
):
    """Show the standings (same view as /ladder leaderboard)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Classement",
                custom_id=f"{_NS}:leaderboard",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderLeaderboardButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the standings."""
        from kingdoms.mods.ladder.surface import LadderSurface

        wiring, ladder_id = _wiring_and_ladder_id(interaction)
        if wiring is None or not ladder_id:
            await interaction.response.send_message("Le ladder n'est pas configuré ici.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        rows = await surface.leaderboard_view(ladder_id)
        if not rows:
            body = "Aucun joueur pour l'instant."
        else:
            body = "\n".join(
                f"{row.rank}. <@{row.user_id}> — {row.rating} ({row.wins}W/{row.losses}L)"
                for row in rows[:10]
            )
        await interaction.response.send_message(body, ephemeral=True)


class LadderRegisterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:register",
):
    """Register on the ladder (core membership, role synced)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="S'inscrire",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:register",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderRegisterButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Register the clicker through the core membership."""
        await _run_membership(interaction, "register")


class LadderUnregisterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:unregister",
):
    """Unregister from the ladder (core membership, role synced)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Se désinscrire",
                custom_id=f"{_NS}:unregister",
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LadderUnregisterButton:
        """Rebuild the stateless item from the wire at click time."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Unregister the clicker through the core membership."""
        await _run_membership(interaction, "unregister")


async def build_ladder_home_view(interaction: discord.Interaction) -> None:
    """Answer the home's mod:ladder click with the button-only ladder home."""
    view = discord.ui.LayoutView(timeout=None)
    main_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    main_row.add_item(LadderRegisterButton())
    main_row.add_item(LadderJoinButton())
    main_row.add_item(LadderLeaveButton())
    info_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    info_row.add_item(LadderQueueButton())
    info_row.add_item(LadderLeaderboardButton())
    staff_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    staff_row.add_item(StaffApplyButton("ladder", label="Nous rejoindre"))
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "## 🗺️ Ladder\n"
                "Ladder saisonnier 1v1 (AoE2) — tout se fait ici, sans commande."
            ),
            main_row,
            info_row,
            discord.ui.Separator(),
            staff_row,
        )
    )
    await interaction.response.send_message(view=view, ephemeral=True)


def register_ladder_home_items(bot: discord.Client) -> None:
    """Register the ladder home's DynamicItems (called at every startup)."""
    bot.add_dynamic_items(
        LadderJoinButton,
        LadderLeaveButton,
        LadderQueueButton,
        LadderLeaderboardButton,
        LadderRegisterButton,
        LadderUnregisterButton,
    )
