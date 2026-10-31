"""The per-game live dashboard (#147): channels, panels, the /live command.

Generic across games (owner direction, 2026-10-23): every game gets its
own live dashboard channel (the tree folder ``games/<game_key>`` hosts
the ``live`` channel and one pinned message, edited in place on every
state change); the /live command and the aoe2 menu panel render the
same dashboard ephemerally. A game with no game service configured
degrades to its empty dashboard — the machinery is game-agnostic.
"""

from __future__ import annotations

import logging
import os
import re
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


def _interaction_locale(interaction: discord.Interaction) -> str:
    """Return the clicker's own client locale (a one-line ack reads nothing)."""
    from kingdoms.discord.locales import interaction_locale

    return interaction_locale(interaction)


class LiveRefreshButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"live:refresh:(?P<game_key>[a-z0-9]+)",
):
    """The ephemeral dashboard's refresh button: re-render on demand.

    The live view is ephemeral-only (owner direction): the click
    rebuilds the view with a fresh snapshot and the SDK updates the
    message carrying the button — never a sibling message.
    """

    def __init__(self, game_key: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                custom_id=f"live:refresh:{game_key}",
                label=label,
                style=discord.ButtonStyle.secondary,
            )
        )
        self.game_key = game_key

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> LiveRefreshButton:
        """Rebuild from the wire; the label rides the original button."""
        button = item if isinstance(item, discord.ui.Button) else None
        label = (button.label if button is not None else None) or "Refresh"
        return cls(match["game_key"], label)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Refresh the message carrying the button — never a sibling.

        No defer: the rebuild is fast (one gRPC read) and an edit-in-
        place through the component's own response is flicker-free
        (the defer-delete dance deleted the view — the 2026-10-30
        lesson). Slow work would defer first; this is not slow work.
        """
        from kingdoms.discord.home import _build_live_view

        view = await _build_live_view(interaction, self.game_key)
        await interaction.response.edit_message(view=view)


def build_dashboard_view(
    snapshot: dict[str, Any],
    body: str,
    bot: discord.Client | None = None,
    locale: str = "en",
    game_key: str = DEFAULT_GAME_KEY,
    page_index: int = 1,
    page_count: int = 1,
) -> discord.ui.LayoutView:
    """Build THE ephemeral dashboard view — the players-panel look.

    A components-v2 Container (accent colour, like an embed) holding
    the body and a RELATIVE TIMESTAMP (Discord renders <t:...:R> as
    '2 minutes ago' — the reader knows at a glance how fresh the
    snapshot is); the refresh button under it; the view id as the
    last line. Ephemeral-only (owner direction): never pinned, never
    a persistent message.
    """
    from kingdoms.discord.message_limits import truncate_body

    title = _t(bot, locale, "title", "🎮 Live dashboard") + f" — {game_label(game_key)}"
    if page_count > 1:
        title += f" ({page_index}/{page_count})"
    colour = 0xE74C3C if snapshot.get("degraded") else 0xF1C40F
    generated_at = int(snapshot.get("generated_at") or 0) or _now_ms()
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                f"# {title}\n\n{truncate_body(body, 3400)}\n\n-# <t:{generated_at // 1000}:R>"
            ),
            accent_colour=discord.Colour(colour),
        )
    )
    from kingdoms.discord.ui.view_footer import add_view_footer

    view.add_item(
        discord.ui.ActionRow(
            LiveRefreshButton(game_key, _t(bot, locale, "refresh", "Refresh")).item
        )
    )
    return add_view_footer(view, f"panel:game:{game_key}:live")


