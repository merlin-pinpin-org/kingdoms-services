"""The AoE2 live activity feed (kingdoms-services#147, owner direction).

Every known player's transition — a linked profile entering a lobby,
starting a game, leaving — lands as ONE message in each concerned
guild's LOGS channel, in arrival order: reading the channel tells the
guild who plays / played AoE2 and when.

- the source: the game service's ``WatchPlayerEvents`` stream (the
  gamedata aggregator folds the providers' events; the enrollments
  subscribe the provider to the guilds' linked profile ids);
- the audience: every guild where the profile's linked platform user
  is a member (the member cache), read from the bindings;
- the transport: the logs channel (``logs_service``), plain text
  lines — the channel is the activity trail, no views, no pins.

The loop is resilient: a stream break reconnects, a send failure
never kills the feed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import discord

logger = logging.getLogger("kingdoms.live_activity")

__all__ = ["start_live_activity_feed"]

_STATE_ICONS = {"in_lobby": "🟡", "in_game": "🟢", "offline": "⚪"}

# the last in-game name seen per profile (the offline event carries no
# match — the label keeps the name the player was last known under)
_LAST_NAME: dict[str, str] = {}


def start_live_activity_feed(bot: discord.Client) -> asyncio.Task[None] | None:
    """Run the AoE2 activity feed: transitions → the guilds' logs channels.

    One stream consumer, forever; None when the game service is not
    wired (the feed degrades to nothing, silently).
    """
    game_key = "aoe2"
    uri = os.environ.get(f"GAME_{game_key.upper()}_URI", "")
    if not uri:
        logger.info("live activity feed: no game service wired — off")
        return None

    async def _run() -> None:
        while True:
            try:
                from kingdoms.game_aoe2.client import GameDataClient

                client = GameDataClient(uri)
                async for event in client.stream_player_events(game_key):
                    await _deliver_event(bot, event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("live activity feed: stream broke — reconnecting", exc_info=True)
                await asyncio.sleep(5)

    task = asyncio.create_task(_run())

    def _log_death(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("live activity feed DIED: %r", task.exception())

    task.add_done_callback(_log_death)
    return task


async def _deliver_event(bot: discord.Client, event: dict[str, Any]) -> None:
    """Render one transition and send it to every concerned guild's logs."""
    profile_id = str(event.get("profile_id", ""))
    user_id = str(event.get("user_id", ""))
    if not profile_id or not user_id:
        return
    line = _render_event_line(event)
    if not line:
        return
    for guild in list(bot.guilds):
        if not await _is_guild_member(bot, guild, user_id):
            continue
        await _send_logs_line(bot, str(guild.id), line)


def _render_event_line(event: dict[str, Any]) -> str:
    """One activity line for one transition, reading-order friendly."""
    match = event.get("match") or {}
    status = str(event.get("new_status", ""))
    old_status = str(event.get("old_status", ""))
    icon = _STATE_ICONS.get(status, "⚪")
    profile_name = _player_label(event)

    if status == "in_lobby":
        url = str(match.get("lobby_url") or "")
        label = str(match.get("match_ref") or "?")
        where = f"[lobby #{label}]({url})" if url else f"lobby #{label}"
        return f"{icon} **{profile_name}** entre dans le {where}"
    if status == "in_game" and old_status == "in_lobby":
        # the launch: the match details ride the same message
        details = _match_details(match)
        url = str(match.get("spectate_url") or "")
        label = str(match.get("match_ref") or "?")
        where = f"[partie #{label}]({url})" if url else f"partie #{label}"
        return f"{icon} **{profile_name}** lance la {where}{details}"
    if status == "in_game":
        url = str(match.get("spectate_url") or "")
        label = str(match.get("match_ref") or "?")
        where = f"[partie #{label}]({url})" if url else f"partie #{label}"
        return f"{icon} **{profile_name}** joue en {where}{_match_details(match)}"
    if status == "offline" and old_status == "in_game":
        return f"{icon} Le match de **{profile_name}** est terminé"
    if status == "offline":
        return f"{icon} **{profile_name}** a quitté le lobby"
    return ""


def _match_details(match: dict[str, Any]) -> str:
    """Render the match context: mode + map on one line, roster under it."""
    bits: list[str] = []
    match_type = str(match.get("match_type") or "")
    fmt = str(match.get("format") or "")
    mode = " ".join(b for b in (match_type, fmt) if b)
    if mode:
        bits.append(f"**{mode}**")
    if match.get("map"):
        bits.append(f"— {match['map']}")
    header = " ".join(bits)
    players = _players_line(match)
    if header and players:
        return f" : {header}\n> {players}"
    if players:
        return f"\n> {players}"
    return f" : {header}" if header else ""


def _players_line(match: dict[str, Any]) -> str:
    """Render the players with their civs and teams (a compact roster)."""
    import json

    raw = match.get("players")
    players = raw if isinstance(raw, list) else []
    if isinstance(raw, str):
        try:
            players = json.loads(raw)
        except (ValueError, TypeError):
            players = []
    parts: list[str] = []
    for p in players:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or p.get("profile_id") or "?")
        civ = str(p.get("civ") or p.get("civilization") or "")
        team = p.get("team")
        bit = name
        if civ:
            bit += f" ({civ})"
        if team not in (None, "", 0):
            bit += f" — équipe {team}"
        parts.append(bit)
    return " · ".join(parts)


def _player_label(event: dict[str, Any]) -> str:
    """Return the transition's subject: the in-game name, else the profile id."""
    match = event.get("match") or {}
    import json

    profile_id = str(event.get("profile_id", ""))
    raw = match.get("players")
    players = raw if isinstance(raw, list) else []
    if isinstance(raw, str):
        try:
            players = json.loads(raw)
        except (ValueError, TypeError):
            players = []
    for p in players:
        if isinstance(p, dict) and str(p.get("profile_id")) == profile_id:
            name = str(p.get("name") or "")
            if name:
                _LAST_NAME[profile_id] = name
                return name
    return _LAST_NAME.get(profile_id, profile_id)


async def _is_guild_member(bot: discord.Client, guild: discord.Guild, user_id: str) -> bool:
    """Answer guild membership through the cache; an unresolvable roster fails open."""
    from kingdoms.discord.member_cache import guild_members

    try:
        members = await guild_members(guild)
    except Exception:
        return True
    if not members:
        return True
    return user_id.isdigit() and int(user_id) in members


async def _send_logs_line(bot: discord.Client, guild_id: str, line: str) -> None:
    """Send one activity line into the guild's logs channel (best-effort)."""
    from kingdoms.core.services.logs import LifecycleEvent

    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return
    try:
        await logs.log_event(guild_id, LifecycleEvent(kind="live_activity", message=line))
    except Exception:
        logger.debug("live activity send skipped (guild %s) — best-effort", guild_id, exc_info=True)
