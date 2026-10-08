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

from kingdoms.mods.ladder.ladder_ids import ladder_id as ladder_id_for

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
        bot: Any = None,
        season_roles: Any = None,
    ) -> None:
        from kingdoms.core.games.aoe2.seed import MongoAoE2Database
        from kingdoms.core.services.game_data import GameDataService
        from kingdoms.core.services.provider_cache import ProviderDataCache
        from kingdoms.core.services.season import SeasonService
        from kingdoms.mods.ladder.match_data import MatchDataService
        from kingdoms.mods.ladder.provider_bridge import LibrematchProviderBridge
        from kingdoms.mods.ladder.service import LadderService

        self.bot = bot
        self.season_roles = season_roles
        self.database = database
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


def build_ladder_wiring(bot: Any = None, season_roles: Any = None) -> LadderWiring | None:
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
        return LadderWiring(database, state, librematch, bot=bot, season_roles=season_roles)
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
    locale = await _locale(interaction)
    has_game_profile = await _join_precondition(user_id)
    if has_game_profile is None:
        reason = _t(
            interaction,
            locale,
            "ladder.join_unconfigured",
            "Registration is not configured — profiles cannot be verified.",
        )
    elif not has_game_profile:
        reason = _t(interaction, locale, "ladder.join_no_profile", "No AoE2 profile linked — use /register first.")
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
        await interaction.response.send_message(
            _t(interaction, locale, "ladder.joined_queue", "You joined the queue."), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            _t(interaction, locale, "ladder.join_failed", "Could not join: {reason}").format(reason=result.reason),
            ephemeral=True,
        )


def register_ladder_commands(
    tree: Any,
    wiring: LadderWiring,
    owner_ref: str,
) -> None:
    """Register the /ladder command group on the command tree."""
    import discord
    from discord import app_commands

    from kingdoms.discord.commands_i18n import localized

    ladder_id = ladder_id_for(GAME_KEY, owner_ref)
    group = app_commands.Group(
        name=localized("commands.ladder_name", "ladder"),
        description=localized("commands.ladder_description", "Ladder: queue, matches, standings"),
    )

    @group.command(name="queue")
    async def queue_command(interaction: discord.Interaction) -> None:
        """Answer /ladder queue with the current ladder queue."""
        from kingdoms.mods.ladder.surface import LadderSurface

        surface = LadderSurface(wiring.service)
        rows = await surface.queue_view(ladder_id, now=_now_ms())
        locale = await _locale(interaction)
        if not rows:
            body = _t(interaction, locale, "ladder.queue_empty", "The queue is empty.")
        else:
            body = "\n".join(
                f"{i + 1}. <@{row.user_id}> — {row.rating} (waiting {row.wait_seconds // 60}m, "
                f"threshold ±{row.threshold})"
                for i, row in enumerate(rows)
            )
        await interaction.response.send_message(body, ephemeral=True)

    from kingdoms.discord.membership_commands import register_membership_commands

    register_membership_commands(group, _ladder_membership(wiring, ladder_id))

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
        locale = await _locale(interaction)
        if result.ok:
            await interaction.response.send_message(
                _t(interaction, locale, "ladder.left_queue", "You left the queue."), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                _t(interaction, locale, "ladder.leave_failed", "Could not leave: {reason}").format(
                    reason=result.reason
                ),
                ephemeral=True,
            )

    @group.command(name="leaderboard")
    async def leaderboard_command(interaction: discord.Interaction) -> None:
        """Answer /ladder leaderboard with the standings."""
        from kingdoms.mods.ladder.surface import LadderSurface

        surface = LadderSurface(wiring.service)
        rows = await surface.leaderboard_view(ladder_id)
        locale = await _locale(interaction)
        if not rows:
            body = _t(interaction, locale, "ladder.leaderboard_empty", "No players yet.")
        else:
            body = "\n".join(
                f"{row.rank}. <@{row.user_id}> — {row.rating} ({row.wins}W/{row.losses}L)" for row in rows[:10]
            )
        await interaction.response.send_message(body, ephemeral=True)

    tree.add_command(group)


def _ladder_membership(wiring: LadderWiring, ladder_id: str) -> Any:
    """Build the ladder's membership (core wiring, guild-independent)."""
    from kingdoms.mods.ladder.membership import build_ladder_membership

    season_roles = wiring.season_roles
    return build_ladder_membership(wiring.service, ladder_id, wiring.season_service, season_roles)


def _now_ms() -> int:
    """Return the current epoch milliseconds."""
    return int(time.time() * 1000)


__all__ = [
    "LadderWiring",
    "build_ladder_wiring",
    "register_ladder_commands",
    "start_ladder_sweep",
]

async def _locale(interaction: Any) -> str:
    """Resolve the answering locale: the guild's, or the user's in DM."""
    logs = getattr(interaction.client, "logs_service", None)
    if logs is None:
        return "en"
    if interaction.guild_id is not None:
        locale: str = await logs.get_locale(str(interaction.guild_id))
        return locale
    user_locale: str = await logs.get_user_locale(str(interaction.user.id))
    return user_locale


def _t(interaction: Any, locale: str, key: str, fallback: str) -> str:
    """Render a catalog key with an inline fallback (never raises)."""
    catalog = getattr(interaction.client, "messages", None)
    if catalog is None:
        return fallback
    return str(catalog.render(key, locale))
    return catalog.render(key, locale) or fallback
