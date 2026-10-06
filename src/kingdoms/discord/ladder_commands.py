"""Discord wiring for the ladder mod: service, live provider, commands, sweep.

Built lazily from the environment (Mongo + Redis configured). Everything
degrades to None when the stores are absent (unit tests, local runs) —
the bot starts regardless.

The daily sweep (provider freshness contract) runs as a background task
started by the bot's setup, not from this module's construction.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

    from kingdoms.core.services.game_data import GameDataService
    from kingdoms.core.services.state import StateService
    from kingdoms.ext_librematch.adapter import LibrematchAdapter
    from kingdoms.mods.ladder.provider_bridge import LibrematchProviderBridge

logger = logging.getLogger(__name__)

GAME_KEY = "aoe2"
SWEEP_INTERVAL_S = 3600


class LadderWiring:
    """The ladder stack wired for one bot process."""

    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        state: StateService,
        librematch: LibrematchAdapter,
    ) -> None:
        from kingdoms.core.games.aoe2.seed import MongoAoE2Database
        from kingdoms.core.services.game_data import GameDataService
        from kingdoms.core.services.provider_cache import ProviderDataCache
        from kingdoms.core.services.season import SeasonService
        from kingdoms.mods.ladder.match_data import MatchDataService
        from kingdoms.mods.ladder.provider_bridge import LibrematchProviderBridge
        from kingdoms.mods.ladder.service import LadderService

        adapter = MongoAoE2Database(database)
        self.game_data: GameDataService = GameDataService(adapter)
        self.season_service: SeasonService | None = SeasonService(adapter, self.game_data)
        self.provider_cache = ProviderDataCache(state)
        bridge = LibrematchProviderBridge(librematch)
        self.match_data = MatchDataService(
            self.provider_cache, game_key=GAME_KEY, cold_provider=bridge, live_provider=bridge
        )
        self.providers: list[LibrematchProviderBridge] = [bridge]
        self.service = LadderService(adapter, self.game_data, match_data=self.match_data)

    async def enrich_completed(self, match_ref: str, match_doc: dict[str, Any]) -> dict[str, Any] | None:
        """Enrich a completed match through the cold provider seam."""
        return await self.match_data.enrich_completed_match(match_ref, match_doc)

    async def sweep_once(self) -> int:
        """Run one daily-sweep pass over the registered profiles."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        database = get_async_database()
        profile_ids: list[str] = []
        async for doc in database[PROFILE_BINDINGS_COLLECTION].find({"game_key": GAME_KEY}):
            profile_id = str(doc.get("profile_id", ""))
            if profile_id:
                profile_ids.append(profile_id)
        if not profile_ids:
            return 0
        report = await self.match_data.sweep_registered_profiles(profile_ids, self.providers[0].fetch_player_stats)
        return report.enriched


def build_ladder_wiring() -> LadderWiring | None:
    """Build the wiring from env; None when Mongo/Redis are not configured."""
    if not os.environ.get("MONGO_URI") or not os.environ.get("REDIS_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.state import StateService
        from kingdoms.ext_librematch.adapter import LibrematchAdapter

        database = get_async_database()
        state = StateService(redis_uri=os.environ["REDIS_URI"])
        librematch = LibrematchAdapter(api_key=os.environ.get("AOE2_API_KEY", ""))
        return LadderWiring(database, state, librematch)
    except Exception:
        logger.exception("LADDER WIRING FAILED — ladder commands stay unavailable")
        return None


def start_ladder_sweep(wiring: LadderWiring) -> asyncio.Task[None]:
    """Run the provider freshness sweep every hour, forever, quietly."""

    async def _loop() -> None:
        while True:
            try:
                enriched = await wiring.sweep_once()
                if enriched:
                    logger.info("ladder sweep refreshed %s profiles", enriched)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("ladder sweep failed", exc_info=True)
            await asyncio.sleep(SWEEP_INTERVAL_S)

    return asyncio.create_task(_loop())


async def _join_precondition(user_id: str) -> bool | None:
    """Read the 'linked game profile' precondition through the registration wiring.

    Returns None when the registration stack is not wired (Mongo/Redis
    absent): the caller answers with an explicit error, never a silent
    allow-through.
    """
    from kingdoms.discord.registration import _wiring as _registration_wiring

    _, registration = _registration_wiring()
    if registration is None:
        return None
    return await registration.has_any_profile(user_id)


async def _join_command(interaction: Any, service: Any, ladder_id: str) -> None:
    """Run the /ladder join flow: profile precondition, then queue."""
    from kingdoms.mods.ladder.surface import ACTION_JOIN_QUEUE, LadderSurface

    user_id = str(interaction.user.id)
    has_game_profile = await _join_precondition(user_id)
    if has_game_profile is None:
        reason = "Registration is not configured — profiles cannot be verified."
    elif not has_game_profile:
        reason = "No AoE2 profile linked — use /register first."
    else:
        reason = ""
    if reason:
        await interaction.response.send_message(reason, ephemeral=True)
        return
    surface = LadderSurface(service)
    result = await surface.execute(
        ACTION_JOIN_QUEUE,
        ladder_id,
        user_id,
        now=_now_ms(),
        has_game_profile=bool(has_game_profile),
    )
    if result.ok:
        await interaction.response.send_message("You joined the queue.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Could not join: {result.reason}", ephemeral=True)


def register_ladder_commands(
    tree: Any,
    wiring: LadderWiring,
    owner_ref: str,
) -> None:
    """Register the /ladder command group on the command tree."""
    import discord
    from discord import app_commands

    ladder_id = f"ladder:{GAME_KEY}:{owner_ref}"
    group = app_commands.Group(name="ladder", description="Ladder: queue, matches, standings")

    @group.command(name="queue")
    async def queue_command(interaction: discord.Interaction) -> None:
        """Answer /ladder queue with the current ladder queue."""
        from kingdoms.mods.ladder.surface import LadderSurface

        surface = LadderSurface(wiring.service)
        rows = await surface.queue_view(ladder_id, now=_now_ms())
        if not rows:
            body = "Queue is empty."
        else:
            body = "\n".join(
                f"{i + 1}. <@{row.user_id}> — {row.rating} (waiting {row.wait_seconds // 60}m, "
                f"threshold ±{row.threshold})"
                for i, row in enumerate(rows)
            )
        await interaction.response.send_message(body, ephemeral=True)

    @group.command(name="register")
    async def register_command(interaction: discord.Interaction) -> None:
        """Register on the ladder and get the season player role."""
        await _register_command(interaction, wiring.service, ladder_id)

    @group.command(name="unregister")
    async def unregister_command(interaction: discord.Interaction) -> None:
        """Unregister from the ladder (leaves the queue, drops the role)."""
        await _unregister_command(interaction, wiring.service, ladder_id)

    @group.command(name="join")
    async def join_command(interaction: discord.Interaction) -> None:
        """Join the ladder queue after the profile precondition."""
        await _join_command(interaction, wiring.service, ladder_id)

    @group.command(name="leave")
    async def leave_command(interaction: discord.Interaction) -> None:
        """Leave the ladder queue."""
        from kingdoms.mods.ladder.surface import ACTION_LEAVE_QUEUE, LadderSurface

        user_id = str(interaction.user.id)
        surface = LadderSurface(wiring.service)
        result = await surface.execute(ACTION_LEAVE_QUEUE, ladder_id, user_id, now=_now_ms())
        if result.ok:
            await interaction.response.send_message("You left the queue.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Could not leave: {result.reason}", ephemeral=True)

    @group.command(name="leaderboard")
    async def leaderboard_command(interaction: discord.Interaction) -> None:
        """Answer /ladder leaderboard with the standings."""
        from kingdoms.mods.ladder.surface import LadderSurface

        surface = LadderSurface(wiring.service)
        rows = await surface.leaderboard_view(ladder_id)
        if not rows:
            body = "No players yet."
        else:
            body = "\n".join(
                f"{row.rank}. <@{row.user_id}> — {row.rating} ({row.wins}W/{row.losses}L)" for row in rows[:10]
            )
        await interaction.response.send_message(body, ephemeral=True)

    tree.add_command(group)


async def _register_command(interaction: Any, service: Any, ladder_id: str) -> None:
    """Run the /ladder register flow: register + sync the season role."""
    user_id = str(interaction.user.id)
    player = await service.register_player(ladder_id, user_id, interaction.user.display_name, now=_now_ms())
    await _sync_player_role(interaction, member=True)
    await interaction.response.send_message(
        f"Inscrit sur le ladder (rating initial {player.rating}).",
        ephemeral=True,
    )


async def _unregister_command(interaction: Any, service: Any, ladder_id: str) -> None:
    """Run the /ladder unregister flow: leave + remove + drop the role."""
    user_id = str(interaction.user.id)
    player = await service.get_player(ladder_id, user_id)
    if player is None:
        await interaction.response.send_message("Tu n'es pas inscrit sur le ladder.", ephemeral=True)
        return
    try:
        await service.leave_queue(ladder_id, user_id)
    except Exception:
        logger.debug("leave_queue before unregister was a no-op", exc_info=True)
    await service.remove_player(ladder_id, user_id)
    await _sync_player_role(interaction, member=False)
    await interaction.response.send_message("Désinscrit du ladder.", ephemeral=True)


async def _sync_player_role(interaction: Any, member: bool) -> None:
    """Sync the season player role after a registration change (best-effort)."""
    from kingdoms.discord.bot.factory import KingdomsBot

    bot = interaction.client
    if not isinstance(bot, KingdomsBot) or bot.season_roles_service is None:
        return
    guild_id = str(interaction.guild_id) if interaction.guild_id is not None else ""
    season = await _active_season_label(bot)
    await bot.season_roles_service.sync_player_role(guild_id, str(interaction.user.id), season, member=member)


async def _active_season_label(bot: Any) -> str:
    """Read the active season label of the wired ladder (best-effort, 's1')."""
    try:
        from kingdoms.core.services.season_roles import season_label

        season_service = getattr(bot, "season_service", None)
        if season_service is None:
            return "s1"
        ladder_id = getattr(bot, "_ladder_id", None)
        if ladder_id is None:
            return "s1"
        season = await season_service.get_active_season(ladder_id)
        if season is None:
            return "s1"
        return season_label({"label": season.label, "name": season.name, "season_id": season.id})
    except Exception:
        return "s1"


def _now_ms() -> int:
    """Return the current epoch milliseconds."""
    return int(time.time() * 1000)


__all__ = [
    "LadderWiring",
    "build_ladder_wiring",
    "register_ladder_commands",
    "start_ladder_sweep",
]
