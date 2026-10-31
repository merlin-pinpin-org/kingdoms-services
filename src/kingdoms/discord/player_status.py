"""The bot-side player status bridge: Live stream → mod notifications.

svc-core aggregates the provider events into normalized player
transitions (``kingdoms.core.services.player_events``) and streams them
over ``WatchPlayerEvents``. This module owns the bot-side half of the
generic mechanism:

- one module-level ``PlayerStatusRouter`` (the core, game-agnostic
  dispatcher) — every mod registers its membership seam and sink on it
  at ``register(bot, config)`` time;
- one background loop (started with the Live stack) that feeds every
  incoming transition to the router, which notifies exactly the mods
  where the player is enrolled.

The mechanism is independent of the game and of the provider: mods
never see aoe2lobby, and providers never see mods.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from kingdoms.core.services.player_events import (
    ModMembership,
    PlayerStatusEvent,
    PlayerStatusRouter,
    PlayerStatusSink,
)

logger = logging.getLogger("kingdoms.bot.player_status")

_router = PlayerStatusRouter()


def player_status_router() -> PlayerStatusRouter:
    """Return the process-wide router mods register their sinks onto."""
    return _router


def register_mod_status_listener(
    mod_name: str, membership: ModMembership, sink: PlayerStatusSink
) -> None:
    """Register one mod's enrollment seam and notification sink."""
    _router.register_mod(mod_name, membership, sink)
    logger.info("player status listener registered: mod=%s", mod_name)


def start_player_status_stream(bot: Any) -> asyncio.Task[None] | None:
    """Consume svc-core's player transitions and dispatch them, forever.

    Returns None when the core is not configured (``CORE_URI``) — the
    mods simply receive no live transitions, the bot keeps running.
    """
    game_uri = os.environ.get("GAME_AOE2_URI", "")
    core_uri = os.environ.get("CORE_URI", "")
    if game_uri:
        from kingdoms.game_aoe2.client import GameDataClient

        client: Any = GameDataClient(game_uri)
    elif core_uri:
        from kingdoms.core.rpc.live import LiveClient

        client = LiveClient(core_uri)
    else:
        return None

    async def _run() -> None:
        while True:
            try:
                async for transition in client.stream_player_events():
                    match = transition.get("match") or {}
                    event = PlayerStatusEvent(
                        game_key=transition.get("game_key", "aoe2"),
                        profile_id=transition["profile_id"],
                        user_id=transition.get("user_id", ""),
                        old_status=transition["old_status"],
                        new_status=transition["new_status"],
                        match_ref=transition.get("match_ref", ""),
                        match_url=str(match.get("lobby_url") or match.get("spectate_url") or ""),
                        occurred_at=transition.get("occurred_at", 0),
                    )
                    notified = await _router.dispatch(event)
                    if notified:
                        logger.info(
                            "player %s transition %s→%s dispatched to %s",
                            event.user_id,
                            event.old_status,
                            event.new_status,
                            ",".join(notified),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("player status stream failed — retrying in 5s", exc_info=True)
                await asyncio.sleep(5)

    return asyncio.create_task(_run())
