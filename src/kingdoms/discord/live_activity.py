"""The AoE2 live activity feed (kingdoms-services#147, owner direction).

Every match involving a linked player becomes ONE rich message in each
concerned guild's LOGS channel — a components-v2 layout (the
match-card look) that LIVES with the match:

- the lobby opens: the card shows the players as they join, the join
  link, the setup (mode, map);
- the game launches: the card flips to the watch link, the civs and
  teams ride the roster;
- the match ends: the card closes with the duration and the results
  link.

One message per (guild, match) — updated in place on every transition,
never resent; the channel reads as the guild's AoE2 history, each
match a single block in arrival order.

The source is the game service's ``WatchPlayerEvents`` stream (the
enrollments already subscribe the provider to the guilds' linked
profile ids); the delivery rides the logs channel resolution; the
loop is resilient (a stream break reconnects, a send failure never
kills the feed).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import discord

logger = logging.getLogger("kingdoms.live_activity")

__all__ = ["start_live_activity_feed"]

_STATE_ICONS = {"in_lobby": "🟡", "in_game": "🟢", "offline": "⚪"}

# the live cards: (guild_id, match_ref) -> the sent message — updated
# in place while the match lives, dropped at the end
_CARDS: dict[tuple[str, str], Any] = {}
# the last in-game name seen per profile (labels survive the offline event)
_LAST_NAME: dict[str, str] = {}
# the first-seen time per (guild, match) — the duration line at the end
_MATCH_STARTED_AT: dict[tuple[str, str], float] = {}
# the last known summary per match — the end card keeps the context
_LAST_SUMMARY: dict[str, dict[str, Any]] = {}


def start_live_activity_feed(bot: discord.Client) -> asyncio.Task[None] | None:
    """Run the AoE2 activity feed: match cards in the guilds' logs channels.

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
    """Route one transition to every concerned guild's match card."""
    profile_id = str(event.get("profile_id", ""))
    user_id = str(event.get("user_id", ""))
    if not profile_id or not user_id:
        return
    match = event.get("match") or {}
    _remember_name(profile_id, match)
    ref = str(match.get("match_ref") or "")
    if ref and match.get("players"):
        _LAST_SUMMARY[ref] = match
    for guild in list(bot.guilds):
        if not await _is_guild_member(bot, guild, user_id):
            continue
        await _upsert_card(bot, str(guild.id), event)


def _remember_name(profile_id: str, match: dict[str, Any]) -> None:
    """Keep the last in-game name of a profile (labels survive offline)."""
    for p in _players(match):
        if isinstance(p, dict) and str(p.get("profile_id")) == profile_id:
            name = str(p.get("name") or "")
            if name:
                _LAST_NAME[profile_id] = name


async def _upsert_card(bot: discord.Client, guild_id: str, event: dict[str, Any]) -> None:
    """Create or update the guild's card for the event's match."""
    match = event.get("match") or {}
    status = str(event.get("new_status", ""))
    old_status = str(event.get("old_status", ""))
    profile_id = str(event.get("profile_id", ""))
    match_ref = str(match.get("match_ref") or "")
    # an offline event carries no match: the card to close is the
    # player's live one
    if not match_ref:
        match_ref = _player_live_ref(profile_id)
    if not match_ref:
        return
    key = (guild_id, match_ref)

    if status == "offline":
        # the player left / the match ended: refresh the card one last
        # time then drop it from the live set
        card = _CARDS.get(key)
        if card is not None and old_status == "in_game":
            # the match ended: the closed card keeps the results link
            await _send_card(bot, guild_id, event, match_ref, ended=True)
            _CARDS.pop(key, None)
            _MATCH_STARTED_AT.pop(key, None)
            _LAST_SUMMARY.pop(match_ref, None)
            _forget_player_ref(profile_id)
            return
        if card is not None:
            await _send_card(bot, guild_id, event, match_ref, ended=False)
            _forget_player_ref(profile_id)
            return
        return

    _remember_player_ref(profile_id, match_ref)
    await _send_card(bot, guild_id, event, match_ref, ended=False)


async def _send_card(
    bot: discord.Client,
    guild_id: str,
    event: dict[str, Any],
    match_ref: str,
    ended: bool,
) -> None:
    """Send (create) or edit (update) the guild's card for one match."""
    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return
    channel_id = await _resolve_logs_channel(bot, guild_id)
    if channel_id is None:
        return
    key = (guild_id, match_ref)
    if key not in _MATCH_STARTED_AT:
        _MATCH_STARTED_AT[key] = time.monotonic()
    view = _build_card(event, match_ref, ended, _MATCH_STARTED_AT[key])
    card = _CARDS.get(key)
    if card is not None:
        try:
            await card.edit(view=view)
            return
        except Exception:
            logger.info("live activity card edit failed (guild %s, match %s) — recreating", guild_id, match_ref)
            _CARDS.pop(key, None)
    # create: the channel id is resolved, the message rides the logs seam
    try:
        guild = bot.get_guild(int(guild_id))
        channel = guild.get_channel(int(channel_id)) if guild else None
        if channel is None or not isinstance(channel, discord.TextChannel):
            return
        message = await channel.send(view=view, silent=True)
        _CARDS[key] = message
    except Exception:
        logger.debug("live activity card send skipped (guild %s) — best-effort", guild_id, exc_info=True)


def _build_card(event: dict[str, Any], match_ref: str, ended: bool, started_at: float) -> Any:
    """Build the match card: a components-v2 container with the live state."""
    match = event.get("match") or _LAST_SUMMARY.get(match_ref) or {}
    players = _players(match)
    mode_bits = [b for b in (str(match.get("match_type") or ""), str(match.get("format") or "")) if b]
    mode = " ".join(dict.fromkeys(mode_bits))
    map_name = str(match.get("map") or "")
    slots = f"{match.get('slots_taken', '?')}/{match.get('slots_total', '?')}"
    icon = "⚪" if ended else _STATE_ICONS.get(str(event.get("new_status", "")), "🟡")
    status_label = "terminé" if ended else ("en partie" if str(event.get("new_status")) == "in_game" else "en lobby")

    title = f"{icon} Partie #{match_ref} — {status_label}"
    header_bits = [b for b in (mode, f"— {map_name}" if map_name else "") if b]
    header = f"**{title}**"
    if header_bits:
        header += "\n" + " ".join(header_bits)
    if not ended:
        header += f" · {slots} slots"

    lines = [header]
    roster = _roster_line(players)
    if roster:
        lines.append(f"> {roster}")
    if ended:
        duration_s = max(0, int(time.monotonic() - started_at))
        lines.append(f"> ⏱️ durée : {duration_s // 60} min {duration_s % 60:02d}s")
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            accent_colour=discord.Colour(
                0x9B59B6 if ended else (0x2ECC71 if str(event.get("new_status")) == "in_game" else 0xF1C40F)
            ),
        )
    )
    row = _card_buttons(match, ended, str(event.get("new_status", "")))
    if row is not None:
        view.add_item(row)
    return view


def _card_buttons(match: dict[str, Any], ended: bool, status: str) -> Any:
    """Build the action row per state: lobby → join, started → watch/download, ended → results."""
    lobby_url = str(match.get("lobby_url") or "")
    spectate_url = str(match.get("spectate_url") or "")
    deep_link = str(match.get("deep_link") or "")
    buttons: list[discord.ui.Button[Any]] = []
    if not ended:
        if status == "in_lobby" and lobby_url:
            buttons.append(discord.ui.Button(label="🚪 Rejoindre", url=lobby_url, style=discord.ButtonStyle.link))
        if status == "in_game" and spectate_url:
            buttons.append(discord.ui.Button(label="👀 Regarder", url=spectate_url, style=discord.ButtonStyle.link))
        if status == "in_game" and deep_link:
            buttons.append(
                discord.ui.Button(label="⬇️ Télécharger", url=deep_link, style=discord.ButtonStyle.link)
            )
    if ended and (spectate_url or lobby_url):
        buttons.append(
            discord.ui.Button(label="📊 Résultats", url=(spectate_url or lobby_url), style=discord.ButtonStyle.link)
        )
    if not buttons:
        return None
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for button in buttons:
        row.add_item(button)
    return row


def _roster_line(players: list[dict[str, Any]]) -> str:
    """Render the players with their civs and teams, one compact line."""
    parts: list[str] = []
    for p in players:
        name = str(p.get("name") or p.get("profile_id") or "?")
        civ = str(p.get("civ") or "")
        team = p.get("team")
        bit = name
        if civ:
            bit += f" ({civ})"
        if team not in (None, "", 0):
            bit += f" — équipe {team}"
        parts.append(bit)
    return " · ".join(parts)


def _players(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the match's players payload (decoded from JSON when needed)."""
    raw = match.get("players")
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return [p for p in decoded if isinstance(p, dict)]
        except (ValueError, TypeError):
            return []
    return []


# the profile -> live match index (an offline event carries no match)
_PLAYER_REF: dict[str, str] = {}


def _remember_player_ref(profile_id: str, match_ref: str) -> None:
    _PLAYER_REF[profile_id] = match_ref


def _forget_player_ref(profile_id: str) -> None:
    _PLAYER_REF.pop(profile_id, None)


def _player_live_ref(profile_id: str) -> str:
    return _PLAYER_REF.get(profile_id, "")


async def _resolve_logs_channel(bot: discord.Client, guild_id: str) -> str | None:
    """Resolve the guild's logs channel through the logs service."""
    logs = getattr(bot, "logs_service", None)
    if logs is None:
        return None
    try:
        resolved = await logs.resolve_channel(guild_id)
    except Exception:
        return None
    return str(resolved) if resolved else None


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
