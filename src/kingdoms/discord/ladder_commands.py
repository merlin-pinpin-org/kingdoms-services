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
        from kingdoms.mods.ladder.match_data import MatchDataService
        from kingdoms.mods.ladder.provider_bridge import LibrematchProviderBridge
        from kingdoms.mods.ladder.service import LadderService

        adapter = MongoAoE2Database(database)
        self.game_data: GameDataService = GameDataService(adapter)
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

    @group.command(name="join")
    async def join_command(interaction: discord.Interaction) -> None:
        """Join the ladder queue after the profile precondition."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION
        from kingdoms.mods.ladder.surface import ACTION_JOIN_QUEUE, LadderSurface

        user_id = str(interaction.user.id)
        database = get_async_database()
        binding = await database[PROFILE_BINDINGS_COLLECTION].find_one({"user_id": user_id})
        if binding is None:
            await interaction.response.send_message("No AoE2 profile linked — use /register first.", ephemeral=True)
            return
        surface = LadderSurface(wiring.service)
        result = await surface.execute(
            ACTION_JOIN_QUEUE,
            ladder_id,
            user_id,
            now=_now_ms(),
            has_game_profile=True,
        )
        if result.ok:
            await interaction.response.send_message("You joined the queue.", ephemeral=True)
        else:
            await interaction.response.send_message(f"Could not join: {result.reason}", ephemeral=True)

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


def _now_ms() -> int:
    """Return the current epoch milliseconds."""
    return int(time.time() * 1000)


__all__ = [
    "LadderWiring",
    "build_ladder_wiring",
    "register_ladder_commands",
    "start_ladder_sweep",
]
