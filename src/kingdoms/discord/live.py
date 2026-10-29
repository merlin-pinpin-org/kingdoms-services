"""The per-game live dashboard (#147): channels, panels, the /live command.

Generic across games (owner direction, 2026-10-23): every game gets its
own live dashboard channel (the tree folder ``games/<game_key>`` hosts
the ``live`` channel and one pinned message, edited in place on every
state change); the /live command and the aoe2 menu panel render the
same dashboard ephemerally. A game with no game service configured
degrades to its empty dashboard — the machinery is game-agnostic.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

import discord

logger = logging.getLogger("kingdoms.live")

# The live machinery is game-generic: the game key rides the channel
# keys, the tree scope and every snapshot call. aoe2 is the first game.
DEFAULT_GAME_KEY = "aoe2"


def live_view_key(game_key: str) -> str:
    """Return the logical message key of a game's pinned dashboard message."""
    return f"view:{game_key}:live"


def live_page_key(game_key: str, page: int) -> str:
    """Return the logical message key of one dashboard page (2..N)."""
    suffix = "" if page <= 1 else f":{page}"
    return f"{live_view_key(game_key)}{suffix}"

_STATE_ICONS = {"offline": "⚫", "in_lobby": "🟡", "in_game": "🟢"}

_LABELS = {"watch": ("Regarder", "Watch"), "join": ("Rejoindre", "Join")}


def _live_label(key: str, locale: str = "fr") -> str:
    """Render one short label (fr default, en variant) — the catalog-less fallback."""
    fr, en = _LABELS[key]
    return en if locale == "en" else fr


OFFLINE_GRACE_MS = 3600 * 1000


def _t(bot: Any, locale: str, key: str, fallback: str) -> str:
    """Render one live-dashboard string in the guild's locale (fallback)."""
    catalog = getattr(bot, "messages", None)
    if catalog is None:
        return fallback
    try:
        rendered = catalog.render(f"live.{key}", locale)
    except Exception:
        return fallback
    return rendered if rendered != f"live.{key}" else fallback


async def scope_snapshot_to_guild(
    snapshot: dict[str, Any],
    guild: Any,
) -> dict[str, Any]:
    """Keep only the players whose platform user is a member of the guild.

    The membership rides the member cache (one chunked fetch per guild
    per hour, kept fresh by the member events) — the dashboard render
    stays fast; a missing membership never crashes it, the player is
    simply out of this guild's dashboard.
    """
    if guild is None:
        return snapshot

    from kingdoms.discord.member_cache import guild_members

    members = await guild_members(guild)
    if not members:
        return snapshot  # unresolvable roster — fail open, never blank
    kept = [
        player
        for player in snapshot.get("players", [])
        if str(player.get("user_id", "")).isdigit()
        and int(str(player.get("user_id", ""))) in members
    ]
    return {**snapshot, "players": kept}


def render_dashboard(
    snapshot: dict[str, Any],
    locale: str = "en",
    stats: dict[str, dict[str, Any]] | None = None,
    now_ms: int | None = None,
    bot: Any = None,
) -> str:
    """Render the dashboard snapshot as a plain-text message body.

    One line per Discord user: their linked profiles are fused into a
    single record and a user is active when any of their profiles is.
    Shown: every active user, plus the users offline for less than an
    hour (with their last completed match timestamp); users offline for
    longer are omitted.
    """
    lines: list[str] = []
    degraded_fb = "⚠️ Providers unreachable — states may be stale, all shown offline."
    if snapshot.get("degraded"):
        lines.append(_t(bot, locale, "degraded", degraded_fb))
    players = snapshot.get("players", [])
    if not players:
        lines.append(
            _t(
                bot,
                locale,
                "no_players",
                "No linked players yet — add your AoE2 profile from the profile menu (/home → Profile).",
            )
        )
        return "\n".join(lines)
    now = now_ms if now_ms is not None else _now_ms()
    accounts = group_by_account(players, stats)
    accounts_by_profile = {
        p["profile_id"]: accounts[p["user_id"]]
        for p in players
        if p.get("user_id") in accounts and p.get("profile_id")
    }

    # one section per match (owner direction): the active players grouped
    # by their match_ref, the section header carrying the match context
    active_players = [p for p in players if p.get("state") != "offline"]
    by_match: dict[str, list[dict[str, Any]]] = {}
    for player in active_players:
        by_match.setdefault(str(player.get("match_ref", "")), []).append(player)
    for match_ref in sorted(by_match):
        if not match_ref:
            for player in by_match[match_ref]:
                lines.append(_render_account(accounts[player["user_id"]], accounts_by_profile, stats))
            continue
        lines.append(_render_match_section(by_match[match_ref], accounts, accounts_by_profile, stats, bot, locale))

    # the offline tail: the members who were recently in a match
    visible = [a for a in accounts.values() if a["active"] or now - a["last_match_ms"] < OFFLINE_GRACE_MS]
    offline = [a for a in visible if not a["active"]]
    if offline:
        lines.append(_t(bot, locale, "offline_separator", "— offline —"))
        for account in sorted(offline, key=_account_sort_key):
            lines.append(_render_account(account, accounts_by_profile, stats or {}))
    if not by_match and not offline:
        lines.append(_t(bot, locale, "all_offline", "Everyone is offline for more than an hour."))
    return "\n".join(lines)


def _render_match_section(
    players: list[dict[str, Any]],
    accounts: dict[str, dict[str, Any]],
    accounts_by_profile: dict[str, dict[str, Any]],
    stats: dict[str, dict[str, Any]] | None,
    bot: Any,
    locale: str,
) -> str:
    """Render one match section: header, then the players.

    The header carries the match context (mode, map, link); each player
    line carries the player (mention, name, elo, civ, enrolled
    co-players) — no repetition between the two.
    """
    first = players[0]
    match = first.get("match") or {}
    state_icon = _STATE_ICONS.get(str(first.get("state", "")), "⚫")
    mode_bits: list[str] = []
    match_type = str(match.get("match_type", ""))
    if match_type:
        mode_bits.append(match_type)
    fmt = str(match.get("format", ""))
    if fmt and fmt != match_type:
        mode_bits.append(fmt)
    header = f"{state_icon} **{' '.join(mode_bits)}**" if mode_bits else state_icon
    if match.get("map"):
        header += f" — {match['map']}"
    links = _match_links(match, str(first.get("state", "")))
    if links:
        header += f" · {' · '.join(links)}"
    lines = [header]
    for player in players:
        account = accounts.get(str(player.get("user_id", "")))
        if account is None:
            continue
        lines.append(_render_section_player(player, account, accounts_by_profile, stats))
    return "\n".join(lines)


def _render_section_player(
    player: dict[str, Any],
    account: dict[str, Any],
    accounts_by_profile: dict[str, dict[str, Any]],
    stats: dict[str, dict[str, Any]] | None,
) -> str:
    """One player line inside a match section: mention, name, elo, civ, co-players.

    The match context (map, link) lives on the section header — the line
    carries the player only.
    """
    match = player.get("match") or {}
    profile_id = str(player.get("profile_id", ""))
    display_name = str((stats or {}).get(profile_id, {}).get("display_name", "")) or profile_id
    line = f"  <@{account['user_id']}> · **{display_name}**"
    elo = _profile_elo(stats or {}, profile_id)
    if elo:
        line += f" · {elo}"
    civ = _profile_civ(match, str(player.get("profile_id", "")))
    if civ:
        line += f" · {civ}"
    others = _enrolled_others(match, str(player.get("profile_id", "")), accounts_by_profile)
    if others:
        line += " · " + " · ".join(others)
    return line


def group_by_account(
    players: list[dict[str, Any]],
    stats: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Fold the flat per-profile list into per-Discord-account records."""
    stats = stats or {}
    accounts: dict[str, dict[str, Any]] = {}
    for p in players:
        user_id = str(p.get("user_id", ""))
        profile_id = str(p.get("profile_id", ""))
        account = accounts.setdefault(
            user_id,
            {"user_id": user_id, "profiles": [], "active": False, "last_match_ms": 0},
        )
        profile_stats = stats.get(profile_id, {})
        state = str(p.get("state", "offline"))
        account["profiles"].append(
            {
                "profile_id": profile_id,
                "state": state,
                "match_ref": str(p.get("match_ref", "")),
                "since": p.get("since", 0),
                "display_name": str(profile_stats.get("display_name", "")) or profile_id,
                "boards": profile_stats.get("boards", []),
                "last_match_ms": profile_stats.get("last_match_ms", 0),
                "match": dict(p.get("match") or {}),
            }
        )
        if state in ("in_lobby", "in_game"):
            account["active"] = True
        account["last_match_ms"] = max(account["last_match_ms"], profile_stats.get("last_match_ms", 0))
    return accounts


def _account_sort_key(account: dict[str, Any]) -> tuple[int, int]:
    """Active accounts first, then most recent last match."""
    return (0 if account["active"] else 1, -account["last_match_ms"])


def _render_account(
    account: dict[str, Any],
    accounts_by_profile: dict[str, dict[str, Any]] | None = None,
    stats: dict[str, dict[str, Any]] | None = None,
) -> str:
    """One block per Discord user: mention header, then the active match lines.

    Readability (owner direction): the mention and the display name on
    ONE line, the match context on the next — a clean two-line block
    per active player, no emoji salad, the links inline at the end.
    """
    lines: list[str] = []
    for profile in account["profiles"]:
        if profile["state"] == "offline":
            continue
        lines.append(_render_active_profile(account, profile, accounts_by_profile, stats))
    if lines:
        return "\n".join(lines)
    header = f"⚫ <@{account['user_id']}>"
    if account["last_match_ms"]:
        header += f" · {_t(None, '', 'last_match', 'dernière partie')} <t:{account['last_match_ms'] // 1000}:R>"
    return header


def _render_active_profile(
    account: dict[str, Any],
    profile: dict[str, Any],
    accounts_by_profile: dict[str, dict[str, Any]] | None = None,
    stats: dict[str, dict[str, Any]] | None = None,
) -> str:
    """One block per active profile — readable, dense, no decoration noise.

    Line 1: state icon, mention, display name, elo.
    Line 2 (indented): map, format, civ, the enrolled co-players
    (🤝 with / ⚔️ against), the aoe2lobby link.
    """
    match = profile.get("match") or {}
    state_icon = _STATE_ICONS.get(profile["state"], "⚫")
    line1 = f"{state_icon} <@{account['user_id']}> · **{profile['display_name']}**"
    elo = _profile_elo(stats or {}, profile["profile_id"])
    if elo:
        line1 += f" · {elo}"
    details: list[str] = []
    if match.get("map"):
        details.append(str(match["map"]))
    if match.get("format"):
        details.append(str(match["format"]))
    civ = _profile_civ(match, profile["profile_id"])
    if civ:
        details.append(civ)
    others = _enrolled_others(match, profile["profile_id"], accounts_by_profile or {})
    details.extend(others)
    links = _match_links(match, profile["state"])
    line2 = "  > " + " · ".join(details) if details else ""
    if links:
        line2 = f"{line2} · {' | '.join(links)}" if line2 else "  > " + " | ".join(links)
    return line1 + (f"\n{line2}" if line2 else "")


def _profile_civ(match: dict[str, Any], profile_id: str) -> str:
    """Resolve the profile's civilization from the match's player slots.

    The summary's players ride as structured slots (profile_id, name,
    civ, team) — the slot carrying this profile id holds its civ.
    """
    for entry in _match_players(match):
        if entry.get("profile_id") == profile_id and entry.get("civ"):
            return str(entry["civ"])
    return ""


def _profile_elo(stats: dict[str, dict[str, Any]], profile_id: str) -> str:
    """Render the profile's best-board elo (the 1v1 ladder when present)."""
    boards = (stats.get(profile_id) or {}).get("boards") or []
    for board in boards:
        if str(board.get("key", "")) == "rm_1v1" or "1v1" in str(board.get("label", "")):
            return f"{board.get('rating', '')} elo"
    return ""


def _enrolled_others(
    match: dict[str, Any],
    profile_id: str,
    accounts_by_profile: dict[str, dict[str, Any]],
) -> list[str]:
    """Render the other enrolled players of the match: with, then against.

    Same team = with (🤝), other team = against (⚔️); each renders as
    the Discord mention of the account owning that profile.
    """
    my_team = 0
    for entry in _match_players(match):
        if entry.get("profile_id") == profile_id:
            my_team = int(entry.get("team", 0) or 0)
            break
    with_me: list[str] = []
    against_me: list[str] = []
    for entry in _match_players(match):
        other_id = str(entry.get("profile_id", ""))
        if not other_id or other_id == profile_id:
            continue
        account = accounts_by_profile.get(other_id)
        if account is None:
            continue
        mention = f"<@{account['user_id']}>"
        if int(entry.get("team", 0) or 0) == my_team:
            with_me.append(f"🤝 {mention}")
        else:
            against_me.append(f"⚔️ {mention}")
    return with_me + against_me


def _match_players(match: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the match summary's player slots (structured)."""
    players = match.get("players")
    if isinstance(players, list):
        return [p for p in players if isinstance(p, dict)]
    return []


def _match_links(match: dict[str, Any], state: str) -> list[str]:
    """Render the match's aoe2lobby link (one, labelled by state).

    The aoe2de:// deep link does not render as clickable in Discord
    (non-http schemes are stripped) — the aoe2lobby pages carry the
    join/spectate actions themselves.
    """
    if state == "in_game" and match.get("spectate_url"):
        return [f"[{_live_label('watch')}]({match['spectate_url']})"]
    if state == "in_lobby" and match.get("lobby_url"):
        return [f"[{_live_label('join')}]({match['lobby_url']})"]
    return []


def game_label(game_key: str) -> str:
    """Return one game key's display label (the game's own name)."""
    from kingdoms.discord.home import _game_label

    return _game_label(game_key)


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)


class LiveClientLike(Protocol):
    """The dashboard client seam (LiveClient or the GameDataClient)."""

    async def watch(self, game_key: str) -> dict[str, Any]:
        """Fetch the current dashboard snapshot for a game."""
        ...

    def stream_snapshots(self, game_key: str) -> Any:
        """Yield dashboard snapshots continuously (async iterator)."""
        ...


class RegisteredMessageLike(Protocol):
    """A resolved registered message (RegisteredMessageModel)."""

    @property
    def channel_id(self) -> str:
        """The channel the message lives in."""
        ...

    @property
    def message_id(self) -> str:
        """The platform message id."""
        ...


class MessageRegistryServiceLike(Protocol):
    """The message registry seam (MessageRegistryService)."""

    async def resolve(self, platform: str, message_key: str, entity_id: str) -> RegisteredMessageLike | None:
        """Resolve a registered message; None when never registered."""
        ...

    async def register(
        self,
        *,
        platform: str,
        message_key: str,
        entity_id: str,
        channel_id: str,
        message_id: str,
        guild_id: str | None = None,
    ) -> None:
        """Persist (or replace) a registered message."""
        ...


async def collect_profile_stats(
    profile_ids: list[str], game_key: str = DEFAULT_GAME_KEY
) -> dict[str, dict[str, Any]]:
    """Resolve per-profile enrichment: name, provider stats, last match.

    Names come from the profile bindings; ratings/W-L come from the
    provider stats cache (Redis TTL + Mongo, kingdoms.core.profile_stats);
    the last-match time is the latest completed match the profile played.
    All layers degrade — a missing value renders as absent, never errors.
    """
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.profile_stats import ProfileStatsService, stats_entry

    if not profile_ids:
        return {}
    database = get_async_database()
    enriched: dict[str, dict[str, Any]] = {}
    bindings = database["profile_bindings"]
    matches = database["matches"]
    provider_uri = os.environ.get("EXT_LIBREMATCH_URI", "")
    provider = None
    if provider_uri:
        from kingdoms.core.rpc.game_client import GameProviderClient

        provider = GameProviderClient(provider_uri, "ext-librematch", game_key)
    redis_client = None
    redis_uri = os.environ.get("REDIS_URI", "")
    if redis_uri:
        from redis.asyncio import Redis

        redis_client = Redis.from_url(redis_uri, decode_responses=True)
    service = ProfileStatsService(provider, database, redis_client, game_key=game_key) if provider is not None else None
    provider_stats = await service.get_many(profile_ids) if service is not None else {}
    for profile_id in profile_ids:
        binding: dict[str, Any] | None = await bindings.find_one({"game_key": game_key, "profile_id": profile_id})
        display_name = ""
        if binding is not None:
            display_name = str((binding.get("profile") or {}).get("display_name", "")) or str(
                binding.get("display_name", "")
            )
        last_match_ms = await _last_completed_match_ms(matches, profile_id)
        stats = provider_stats.get(profile_id)
        enriched[profile_id] = {
            "display_name": display_name,
            "rating": stats_entry(stats, "rm_1v1", "rating"),
            "wins": stats_entry(stats, "rm_1v1", "wins"),
            "losses": stats_entry(stats, "rm_1v1", "losses"),
            "last_match_ms": last_match_ms,
        }
    return enriched


async def _last_completed_match_ms(matches: Any, profile_id: str) -> int:
    """Resolve the profile's most recent completed-match time (epoch ms).

    Seeded matches carry the host/guest profile ids and a completion time
    (``game_completed_at`` or ``completed_at``); the status is matched
    case-insensitively (``COMPLETED`` seeded vs ``completed`` recorded).
    Zero means no completed match yet.
    """
    last = 0
    query: dict[str, Any] = {
        "$or": [
            {"host_profile_id": profile_id},
            {"guest_profile_id": profile_id},
        ]
    }
    async for doc in matches.find(query):
        if str(doc.get("status", "")).lower() != "completed":
            continue
        for field in ("completed_at", "game_completed_at"):
            value = doc.get(field)
            if isinstance(value, str) and value.isdigit():
                value = int(value)
            if isinstance(value, int) and value > last:
                last = value
    return last


async def ensure_live_dashboard_channel(
    guild: discord.Guild, game_key: str
) -> discord.TextChannel:
    """Resolve the guild's live channel for one game through the tree's stored id.

    The channel lives in the game's tree scope folder (``games/<game_key>``,
    key ``live``), provisioned and persisted by the Panels SDK; the name
    never drives the resolution. Raises LookupError when the tree has
    not provided it yet (the caller degrades quietly).
    """
    from kingdoms.discord.ui.panels_provisioning import stored_channel_id

    stored = await stored_channel_id(str(guild.id), f"games/{game_key}", "live")
    if stored and stored.isdigit():
        channel = guild.get_channel(int(stored))
        if channel is not None and not isinstance(channel, (discord.CategoryChannel, discord.ForumChannel)):
            return channel  # type: ignore[return-value]
    raise LookupError(f"the tree has no live channel yet (games/{game_key}:live)")


async def ensure_live_dashboard(
    bot: discord.Client,
    guild_id: str,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
    game_key: str = DEFAULT_GAME_KEY,
) -> bool:
    """Ensure the guild's dashboard channel holds one up-to-date dashboard message.

    The message is addressed by its logical key (``live-dashboard``)
    through the message registry and edited in place on every refresh —
    never re-sent, so the channel never spams. When the registered
    message is gone (deleted, channel wiped), a new one is created and
    registered. Best-effort: a failure never blocks the bot.
    Returns True when a message was created, False when an existing
    one was refreshed (or the step degraded quietly).
    """
    guild = bot.get_guild(int(guild_id)) if guild_id.isdigit() else None
    if guild is None:
        return False
    try:
        channel = await ensure_live_dashboard_channel(guild, game_key)
    except LookupError:
        return False  # the tree has not provisioned the game's live channel yet
    try:
        snapshot = await client.watch(game_key)
    except Exception:
        logger.warning(
            "live dashboard fetch failed (guild %s, game %s) — best-effort",
            guild_id,
            game_key,
            exc_info=True,
        )
        snapshot = {"players": [], "generated_at": _now_ms(), "degraded": True}
    snapshot = await scope_snapshot_to_guild(snapshot, guild)
    from kingdoms.discord.content_posts import guild_locale

    locale = await guild_locale(guild_id, bot)
    body = render_dashboard(snapshot, locale=locale, bot=bot)
    registered = await _resolve_page(registry, guild_id, game_key)
    page_view = build_dashboard_view(snapshot, body, bot, locale=locale, game_key=game_key)
    if await _edit_registered(channel, registered, None, page_view):
        await _remember_sent_fingerprint(guild_id, _snapshot_fingerprint(snapshot))
        return False
    reused = await _reuse_pinned_dashboard(channel, page_view)
    if reused is not None:
        await registry.register(
            platform=PLATFORM,
            message_key=_page_key(game_key, 1),
            entity_id=guild_id,
            channel_id=str(channel.id),
            message_id=reused,
            guild_id=guild_id,
        )
        await _sweep_duplicate_dashboards(channel, keep=reused)
        return False
    await _sweep_duplicate_dashboards(channel, keep=None)
    message = await channel.send(view=page_view)
    try:
        await message.pin(reason="kingdoms: live dashboard (#147)")
    except Exception:
        logger.warning("live dashboard pin failed (guild %s) — best-effort", guild_id, exc_info=True)
    await registry.register(
        platform=PLATFORM,
        message_key=_page_key(game_key, 1),
        entity_id=guild_id,
        channel_id=str(channel.id),
        message_id=str(message.id),
        guild_id=guild_id,
    )
    return True


def _page_key(game_key: str, page: int) -> str:
    """Build the registry key of one game's dashboard page.

    The view convention is ``view:<game>:live``; extra pages suffix it.
    """
    return live_page_key(game_key, page)


async def _resolve_page(registry: Any, guild_id: str, game_key: str, page: int = 1) -> Any:
    """Resolve a game's dashboard page message."""
    return await registry.resolve(PLATFORM, _page_key(game_key, page), guild_id)


async def _reuse_pinned_dashboard(
    channel: discord.TextChannel, view: discord.ui.LayoutView
) -> str | None:
    """Reuse an already-pinned dashboard message instead of adding a new one.

    The registry may point at a dead message (deleted, channel wiped) while
    the channel still holds the pinned dashboard: adopting it keeps one
    dashboard message per channel instead of accumulating a new message
    per refresh failure.
    """
    try:
        for message in await channel.pins():
            if message.author.id != channel.guild.me.id:
                continue
            if not (message.flags and int(message.flags.value) & (1 << 15)):
                continue  # not a components-v2 dashboard of ours (bit 15 = IS_COMPONENTS_V2)
            await message.edit(content=None, embed=None, view=view)
            return str(message.id)
    except Exception:
        logger.warning("live dashboard pin adoption failed — best-effort", exc_info=True)
    return None


def build_dashboard_view(
    snapshot: dict[str, Any],
    body: str,
    bot: discord.Client | None = None,
    locale: str = "en",
    game_key: str = DEFAULT_GAME_KEY,
    page_index: int = 1,
    page_count: int = 1,
) -> discord.ui.LayoutView:
    """Build THE dashboard view — the players-panel look, one code path.

    A components-v2 Container (accent colour, like an embed) holding the
    body, the view id as a TextDisplay UNDER it. No embed anywhere (an
    embed cannot share a message with components-v2 — 50035). The
    pinned channel, /live and the menu button all send this exact view.
    """
    from kingdoms.discord.message_limits import truncate_body

    title = _t(bot, locale, "title", "🎮 Live dashboard") + f" — {game_label(game_key)}"
    if page_count > 1:
        title += f" ({page_index}/{page_count})"
    colour = 0xE74C3C if snapshot.get("degraded") else 0xF1C40F
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(f"# {title}\n\n{truncate_body(body, 3500)}"),
            accent_colour=discord.Colour(colour),
        )
    )
    from kingdoms.discord.ui.view_footer import add_view_footer

    return add_view_footer(view, f"panel:game:{game_key}:live")


async def _sweep_duplicate_dashboards(channel: discord.TextChannel, keep: str | None) -> int:
    """Delete the duplicate dashboard messages; the kept one stays.

    A crash mid-cycle (or a pre-adoption deployment) can leave an
    orphan dashboard next to the pinned one. The sweep keeps the
    pinned (or freshly created) message and removes the bot's other
    dashboard messages in the channel — the view-id mark identifies
    ours; foreign messages are never touched.
    """
    from kingdoms.discord.pinned_marks import is_pinned_view

    removed = 0
    try:
        async for message in channel.history(limit=50, oldest_first=False):
            if str(message.id) == str(keep or ""):
                continue
            if message.author.id != channel.guild.me.id:
                continue
            if not is_pinned_view(message):
                continue
            if message.pinned:
                continue
            try:
                await message.delete()
                removed += 1
            except Exception:
                logger.debug("dashboard sweep skip (message %s)", message.id, exc_info=True)
    except Exception:
        logger.debug("dashboard sweep failed — best-effort", exc_info=True)
    if removed:
        logger.info("live dashboard: %d duplicate message(s) swept", removed)
    return removed




async def _edit_registered(
    channel: discord.TextChannel,
    registered: RegisteredMessageLike | None,
    embed: discord.Embed | None,
    view: discord.ui.LayoutView,
) -> bool:
    """Edit the registered message in place; True when the edit landed.

    The unified view rides every edit — the footer (the view id) never
    disappears on refresh.
    """
    if registered is None or not str(registered.channel_id).isdigit() or not str(registered.message_id).isdigit():
        return False
    try:
        message = channel.get_partial_message(int(registered.message_id))
        await message.edit(content=None, embed=embed, view=view)
        return True
    except Exception:
        logger.warning(
            "live dashboard edit failed (message %s) — recreating",
            getattr(registered, "message_id", "?"),
            exc_info=True,
        )
        return False


async def start_live_dashboard_refresh(
    bot: discord.Client,
    clients: dict[str, LiveClientLike],
    registry: MessageRegistryServiceLike,
) -> asyncio.Task[None]:
    """Refresh every guild's per-game dashboards: streams + a self-healing interval.

    One stream watcher per game (``clients`` maps a game key to its
    dashboard client); the interval pass remains as the safety net
    (recreating a deleted message, healing a broken stream) — it walks
    every game of every guild.
    """

    async def _run() -> None:
        watchers = [
            asyncio.create_task(_watch_stream(bot, client, registry, game_key))
            for game_key, client in clients.items()
        ]
        try:
            while True:
                for guild in list(bot.guilds):
                    for game_key, client in clients.items():
                        try:
                            await ensure_live_dashboard(bot, str(guild.id), client, registry, game_key)
                        except Exception:
                            logger.warning(
                                "live dashboard refresh failed (guild %s, game %s) — best-effort",
                                guild.id,
                                game_key,
                                exc_info=True,
                            )
                await asyncio.sleep(DASHBOARD_REFRESH_INTERVAL_S)
        finally:
            for watcher in watchers:
                watcher.cancel()

    return asyncio.create_task(_run())


PLATFORM = "discord"
# The dashboard cadence (owner direction: 15 s), configurable through
# the bot's environment without a code change.
DASHBOARD_REFRESH_INTERVAL_S = float(os.environ.get("DASHBOARD_REFRESH_INTERVAL_S", "15"))
DASHBOARD_MIN_EDIT_INTERVAL_S = float(os.environ.get("DASHBOARD_MIN_EDIT_INTERVAL_S", "15"))
_LAST_PUSH: dict[str, tuple[float, str]] = {}
LIVE_STATE_SCOPE = "live-dashboard"


async def _remember_sent_fingerprint(guild_id: str, fingerprint: str) -> None:
    """Persist the last-sent dashboard state (the bot knows, no re-read).

    The state store (Redis cache-aside) holds the fingerprint of the
    last snapshot each guild's dashboard shows; after a restart the
    comparison continues without fetching the message content.
    """
    redis_uri = os.environ.get("REDIS_URI", "")
    if not redis_uri:
        return
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_uri, decode_responses=True)
        await client.set(f"{LIVE_STATE_SCOPE}:{guild_id}", fingerprint)
        await client.close()
    except Exception:
        logger.debug("live dashboard state persist skipped — best-effort", exc_info=True)


async def _last_sent_fingerprint(guild_id: str) -> str | None:
    """Read the persisted last-sent fingerprint; None when never sent."""
    redis_uri = os.environ.get("REDIS_URI", "")
    if not redis_uri:
        return None
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(redis_uri, decode_responses=True)
        value = await client.get(f"{LIVE_STATE_SCOPE}:{guild_id}")
        await client.close()
        return str(value) if value else None
    except Exception:
        return None


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Build a stable fingerprint of the rendered-relevant snapshot fields.

    ``generated_at`` changes on every stream frame; it is excluded —
    the fingerprint tracks the **player states** the dashboard renders,
    so an unchanged roster never triggers an edit (#251).
    """
    players = snapshot.get("players") or []
    parts = [
        str(p.get("profile_id", ""))
        + ":"
        + str(p.get("state", ""))
        + ":"
        + _match_fingerprint(p.get("match") or {})
        for p in players
    ]
    return "|".join(parts) + f"|degraded={snapshot.get('degraded', False)}"


def _match_fingerprint(match: dict[str, Any]) -> str:
    """Hash a player's live match payload (short + stable)."""
    import hashlib
    import json as _json

    try:
        return hashlib.sha256(
            _json.dumps(match, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()[:12]
    except (TypeError, ValueError):
        return ""


async def _watch_stream(
    bot: discord.Client,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
    game_key: str,
) -> None:
    """Consume one game's WatchDashboard stream; every changed frame refreshes.

    Works with either client: the Live seam (svc-core) or the enriched
    GameData client — both expose ``stream_snapshots``. One stream per
    game (each game has its own game service and channels).
    """
    while True:
        try:
            async for snapshot in client.stream_snapshots(game_key):
                await _push_snapshot(bot, client, registry, snapshot, game_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("live dashboard stream broke — reconnecting", exc_info=True)
            await asyncio.sleep(5)


async def _push_snapshot(
    bot: discord.Client,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
    snapshot: dict[str, Any],
    game_key: str = DEFAULT_GAME_KEY,
) -> None:
    """Edit every guild's dashboard message — throttled, on change only.

    Discord caps edits of messages older than one hour (30046): pushing
    every stream frame edits far too often. Each guild edits at most
    once per ``DASHBOARD_MIN_EDIT_INTERVAL_S`` and only when the
    snapshot fingerprint actually changed; the periodic interval pass
    remains the safety net.
    """
    now = time.monotonic()
    fingerprint = _snapshot_fingerprint(snapshot)
    if fingerprint == _LAST_PUSH.get("_global", (0.0, ""))[1]:
        return
    _LAST_PUSH["_global"] = (now, fingerprint)
    active_ids = [
        str(p.get("profile_id", ""))
        for p in snapshot.get("players", [])
        if p.get("state") != "offline"
    ]
    stats = await collect_profile_stats(active_ids)
    body = render_dashboard(snapshot, stats=stats)
    page_view = build_dashboard_view(snapshot, body, bot, game_key=game_key)
    views = [page_view]
    for guild in list(bot.guilds):
        guild_id = str(guild.id)
        last_at, last_fp = _LAST_PUSH.get(guild_id, (0.0, ""))
        if fingerprint == last_fp:
            continue
        if not last_fp:
            persisted = await _last_sent_fingerprint(guild_id)
            if persisted == fingerprint:
                _LAST_PUSH[guild_id] = (now, fingerprint)
                continue
        if now - last_at < DASHBOARD_MIN_EDIT_INTERVAL_S:
            continue
        _LAST_PUSH[guild_id] = (now, fingerprint)
        try:
            channel = await ensure_live_dashboard_channel(guild, game_key)
        except LookupError:
            continue  # the tree has not provisioned the game's live channel yet
        registered = await registry.resolve(PLATFORM, _page_key(game_key, 1), str(guild.id))
        try:
            if not await _edit_registered(channel, registered, None, views[0]):
                await ensure_live_dashboard(bot, guild_id, client, registry, game_key)
            await _remember_sent_fingerprint(guild_id, fingerprint)
        except Exception:
            logger.warning("live dashboard push failed (guild %s) — best-effort", guild.id, exc_info=True)
