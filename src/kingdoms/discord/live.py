"""The /live dashboard command and its dedicated channel (#147).

The live test dashboard: a dedicated ``live-dashboard`` channel hosts one
persistent message showing every linked player's current state
(offline / in lobby / in game, match ref, since when). The message is
edited in place on every update — no spam. ``/live`` renders the same
dashboard ephemerally; ``/game-link`` and ``/game-unlink`` manage the
minimal profile links needed to test the providers (test-scoped, kept
simple per #147).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Protocol

import discord
from discord import app_commands

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.discord.commands_i18n import localized, reply
from kingdoms.discord.message_limits import MAX_EMBED_DESCRIPTION, truncate_body

logger = logging.getLogger("kingdoms.live")

GAME_KEY = "aoe2"
LIVE_CHANNEL_NAME = "📡-live-dashboard"
LIVE_LEGACY_CHANNEL_NAME = "live-dashboard"
LIVE_MESSAGE_KEY = "live-dashboard"

_STATE_ICONS = {"offline": "⚫", "in_lobby": "🟡", "in_game": "🟢"}


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
        lines.append(_t(bot, locale, "no_players", "No linked players yet — link a profile with /game-link."))
        return "\n".join(lines)
    now = now_ms if now_ms is not None else _now_ms()
    accounts = group_by_account(players, stats)
    visible = [a for a in accounts.values() if a["active"] or now - a["last_match_ms"] < OFFLINE_GRACE_MS]
    ordered = sorted(visible, key=_account_sort_key)
    offline_seen = False
    for account in ordered:
        if not offline_seen and not account["active"]:
            lines.append(_t(bot, locale, "offline_separator", "— offline —"))
            offline_seen = True
        lines.append(_render_account(account))
    if not ordered:
        lines.append(_t(bot, locale, "all_offline", "Everyone is offline for more than an hour."))
    return "\n".join(lines)


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
            }
        )
        if state in ("in_lobby", "in_game"):
            account["active"] = True
        account["last_match_ms"] = max(account["last_match_ms"], profile_stats.get("last_match_ms", 0))
    return accounts


def _account_sort_key(account: dict[str, Any]) -> tuple[int, int]:
    """Active accounts first, then most recent last match."""
    return (0 if account["active"] else 1, -account["last_match_ms"])


def _render_account(account: dict[str, Any]) -> str:
    """One line per Discord user: mention, fused state, last-match stamp."""
    icon = "🟢" if account["active"] else "⚫"
    header = f"{icon} <@{account['user_id']}>"
    if account["last_match_ms"]:
        header += f" — last match <t:{account['last_match_ms'] // 1000}:R>"
    return header


def register_live_commands(
    tree: app_commands.CommandTree[discord.Client],
    catalog: MessageCatalog | None = None,
) -> None:
    """Register the /live, /game-link and /game-unlink commands.

    The commands read svc-core's Live service through ``LiveClient``
    when ``CORE_URI`` is set; without it (local runs, providers down),
    the dashboard degrades to a note — the command still answers.
    """
    from kingdoms.core.rpc.live import LiveClient

    core_uri = os.environ.get("CORE_URI", "")
    client = LiveClient(core_uri) if core_uri else None

    def _render_locale(catalog: MessageCatalog | None, locale: str, key: str, fallback: str) -> str:
        if catalog is None:
            return fallback
        return catalog.render(key, locale)

    @tree.command(
        name=localized("commands.live_name", "live"),
        description=localized("commands.live_description", "Live test dashboard: linked players, lobbies and games"),
    )
    async def live_command(interaction: discord.Interaction) -> None:
        """Answer /live with the current dashboard snapshot."""
        await interaction.response.defer(ephemeral=True)
        if client is None:
            await interaction.followup.send(await reply(interaction, "live.unavailable"), ephemeral=True)
            return
        try:
            snapshot = await client.watch("aoe2")
        except Exception:
            logger.warning("live snapshot fetch failed", exc_info=True)
            snapshot = {"players": [], "generated_at": 0, "degraded": True}
        stats = await collect_profile_stats([str(p.get("profile_id", "")) for p in snapshot.get("players", [])])
        body = render_dashboard(snapshot, _locale(interaction), stats=stats)
        await interaction.followup.send(body, ephemeral=True)

    @tree.command(
        name=localized("commands.game_link_name", "game-link"),
        description=localized(
            "commands.game_link_description", "Link your AoE2 profile id to your account (test scope)"
        ),
    )
    @app_commands.describe(profile_id="Your AoE2 profile id")
    async def game_link_command(interaction: discord.Interaction, profile_id: str) -> None:
        """Link the invoker's AoE2 profile (minimal test-scoped link)."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        if interaction.user.id is None or profile_id.strip() == "":
            await interaction.response.send_message(await reply(interaction, "profile_required"), ephemeral=True)
            return
        profile = profile_id.strip()
        binding = {
            "_id": f"binding:aoe2:{interaction.user.id}:{profile}",
            "user_id": str(interaction.user.id),
            "game_key": "aoe2",
            "profile_id": profile,
            "bound_at": int(_now_ms()),
        }
        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        await db.replace_one({"_id": binding["_id"]}, binding, upsert=True)
        await interaction.response.send_message(
            await reply(interaction, "live.linked", profile=profile, user=interaction.user.id),
            ephemeral=True,
        )

    @tree.command(
        name=localized("commands.game_unlink_name", "game-unlink"),
        description=localized("commands.game_unlink_description", "Unlink one of your AoE2 profiles"),
    )
    @app_commands.describe(profile_id="The AoE2 profile id to unlink")
    async def game_unlink_command(interaction: discord.Interaction, profile_id: str) -> None:
        """Remove one of the invoker's AoE2 profile links."""
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

        profile = profile_id.strip()
        db = get_async_database()[PROFILE_BINDINGS_COLLECTION]
        removed = await db.delete_one({"_id": f"binding:aoe2:{interaction.user.id}:{profile}"})
        if removed and removed.deleted_count > 0:
            await interaction.response.send_message(
                await reply(interaction, "live.unlinked", profile=profile), ephemeral=True
            )
        else:
            await interaction.response.send_message(
                await reply(interaction, "live.no_link", profile=profile), ephemeral=True
            )


def _locale(interaction: discord.Interaction) -> str:
    """Best-effort locale: the user's Discord locale, defaulting to en."""
    return str(interaction.locale) if interaction.locale else "en"


def _now_ms() -> int:
    """Epoch milliseconds, the repo's timestamp convention."""
    return int(time.time() * 1000)


class LiveClientLike(Protocol):
    """The svc-core live client seam (LiveClient)."""

    async def watch(self, game_key: str) -> dict[str, Any]:
        """Fetch the current dashboard snapshot for a game."""
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


async def collect_profile_stats(profile_ids: list[str], game_key: str = GAME_KEY) -> dict[str, dict[str, Any]]:
    """Resolve per-profile enrichment: name, provider stats, last match.

    Names come from the profile bindings; ratings/W-L come from the
    provider stats cache (Redis TTL + Mongo, kingdoms.core.profile_stats);
    the last-match time is the latest completed match the profile played.
    All layers degrade — a missing value renders as absent, never errors.
    """
    import os

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


async def ensure_live_dashboard_channel(guild: discord.Guild) -> discord.TextChannel:
    """Resolve, migrate or create the guild's live-dashboard channel (idempotent).

    A legacy ``live-dashboard`` channel (pre-icon) is renamed in place —
    the message registry keeps pointing at the same channel id, so the
    registered dashboard message survives the rename.
    """
    for channel in guild.text_channels:
        if channel.name == LIVE_CHANNEL_NAME:
            return channel
    for channel in guild.text_channels:
        if channel.name == LIVE_LEGACY_CHANNEL_NAME:
            await channel.edit(name=LIVE_CHANNEL_NAME, reason="Kingdoms: live dashboard channel icon (#147)")
            return channel
    return await guild.create_text_channel(LIVE_CHANNEL_NAME, reason="Kingdoms live test dashboard (#147)")


async def ensure_live_dashboard(
    bot: discord.Client,
    guild_id: str,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
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
    channel = await ensure_live_dashboard_channel(guild)
    try:
        snapshot = await client.watch(GAME_KEY)
    except Exception:
        logger.warning("live dashboard fetch failed (guild %s) — best-effort", guild_id, exc_info=True)
        snapshot = {"players": [], "generated_at": _now_ms(), "degraded": True}
    from kingdoms.discord.content_posts import guild_locale

    locale = await guild_locale(guild_id, bot)
    body = render_dashboard(snapshot, locale=locale, bot=bot)
    embeds = _dashboard_embeds(snapshot, body, bot, locale=locale)
    registered = await registry.resolve(PLATFORM, _page_key(1), guild_id)
    if await _edit_registered(channel, registered, embeds[0]):
        await _sync_extra_pages(guild_id, channel, registry, embeds)
        return False
    reused = await _reuse_pinned_dashboard(channel, embeds[0])
    if reused is not None:
        await registry.register(
            platform=PLATFORM,
            message_key=_page_key(1),
            entity_id=guild_id,
            channel_id=str(channel.id),
            message_id=reused,
            guild_id=guild_id,
        )
        await _sync_extra_pages(guild_id, channel, registry, embeds)
        await _sweep_duplicate_dashboards(channel, keep=reused)
        return False
    await _sweep_duplicate_dashboards(channel, keep=None)
    message = await channel.send(embed=embeds[0])
    try:
        await message.pin(reason="kingdoms: live dashboard (#147)")
    except Exception:
        logger.warning("live dashboard pin failed (guild %s) — best-effort", guild_id, exc_info=True)
    await registry.register(
        platform=PLATFORM,
        message_key=_page_key(1),
        entity_id=guild_id,
        channel_id=str(channel.id),
        message_id=str(message.id),
        guild_id=guild_id,
    )
    await _sync_extra_pages(guild_id, channel, registry, embeds)
    return True


def _page_key(page: int) -> str:
    """Build the registry key of one dashboard page (page 1 keeps the legacy key)."""
    return LIVE_MESSAGE_KEY if page == 1 else f"{LIVE_MESSAGE_KEY}-{page}"


def _dashboard_embeds(
    snapshot: dict[str, Any],
    body: str,
    bot: discord.Client | None = None,
    locale: str = "en",
) -> list[discord.Embed]:
    """Build the dashboard embeds: as many as the player list needs.

    Discord caps one embed description at 4096 chars; a long player
    list is split across several messages (one embed each) instead of
    being silently truncated.
    """
    lines = body.split("\n")
    pages: list[str] = []
    current: list[str] = []
    for line in lines:
        candidate = "\n".join([*current, line])
        if len(candidate) > (MAX_EMBED_DESCRIPTION - 128) and current:
            pages.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        pages.append("\n".join(current))
    if not pages:
        pages = [body]
    return [
        _dashboard_embed(
            snapshot, page, bot, page_index=i + 1, page_count=len(pages), locale=locale
        )
        for i, page in enumerate(pages)
    ]


async def _sync_extra_pages(
    guild_id: str,
    channel: discord.TextChannel,
    registry: MessageRegistryServiceLike,
    embeds: list[discord.Embed],
) -> None:
    """Keep pages 2..N alive: edit in place, create, or clean up as needed."""
    for page_number in range(2, len(embeds) + 1):
        key = _page_key(page_number)
        embed = embeds[page_number - 1]
        registered = await registry.resolve(PLATFORM, key, guild_id)
        if await _edit_registered(channel, registered, embed):
            continue
        message = await channel.send(embed=embed)
        await registry.register(
            platform=PLATFORM,
            message_key=key,
            entity_id=guild_id,
            channel_id=str(channel.id),
            message_id=str(message.id),
            guild_id=guild_id,
        )
    from kingdoms.discord.pinned_marks import is_pinned_view

    for page_number in range(len(embeds) + 1, len(embeds) + 10):
        registered = await registry.resolve(PLATFORM, _page_key(page_number), guild_id)
        if registered is None:
            break
        try:
            stale = await channel.get_partial_message(int(registered.message_id)).fetch()
            if is_pinned_view(stale):
                await stale.edit(content=None, embed=None)
                continue
            await channel.get_partial_message(int(registered.message_id)).delete()
        except Exception:
            logger.warning("live dashboard page %s cleanup failed", page_number, exc_info=True)


async def _sweep_duplicate_dashboards(channel: discord.TextChannel, keep: str | None) -> int:
    """Delete the duplicate dashboard messages; the kept one stays.

    A crash mid-cycle (or a pre-adoption deployment) can leave an
    orphan dashboard next to the pinned one. The sweep keeps the
    pinned (or freshly created) message and removes the bot's other
    dashboard messages in the channel — the ``fixe:`` mark identifies
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


async def _reuse_pinned_dashboard(channel: discord.TextChannel, embed: discord.Embed) -> str | None:
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
            if not any("Live dashboard" in (e.title or "") for e in message.embeds):
                continue
            await message.edit(content=None, embed=embed)
            return str(message.id)
    except Exception:
        logger.warning("live dashboard pin adoption failed — best-effort", exc_info=True)
    return None


def _dashboard_embed(
    snapshot: dict[str, Any],
    body: str,
    bot: discord.Client | None = None,
    page_index: int = 1,
    page_count: int = 1,
    locale: str = "en",
) -> discord.Embed:
    """Build one dashboard page's embed: title, icon, and the players' states."""
    user = getattr(bot, "user", None) if bot is not None else None
    icon = getattr(user, "display_avatar", None) if user else None
    icon_url = getattr(icon, "url", None) if icon else None
    from kingdoms.discord.pinned_marks import pinned_mark

    description = truncate_body(body + "\n" + pinned_mark("live-dashboard"), MAX_EMBED_DESCRIPTION)
    title = _t(bot, locale, "title", "🎮 Live dashboard")
    if page_count > 1:
        title += f" ({page_index}/{page_count})"
    embed = discord.Embed(
        title=title,
        description=description,
        colour=discord.Colour(0xF1C40F) if not snapshot.get("degraded") else discord.Colour(0xE74C3C),
    )
    if icon_url:
        embed.set_thumbnail(url=icon_url)
    return embed


async def _edit_registered(
    channel: discord.TextChannel,
    registered: RegisteredMessageLike | None,
    embed: discord.Embed,
) -> bool:
    """Edit the registered message in place; True when the edit landed."""
    if registered is None or not str(registered.channel_id).isdigit() or not str(registered.message_id).isdigit():
        return False
    try:
        message = channel.get_partial_message(int(registered.message_id))
        await message.edit(content=None, embed=embed)
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
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
) -> asyncio.Task[None]:
    """Refresh every guild's dashboard on change: a stream watcher plus a self-healing interval.

    The stream consumes WatchDashboard frames — the server emits one per
    player-state change — and every frame lands on the dashboard within
    seconds. The interval pass remains as the safety net (recreating a
    deleted message, healing a broken stream).
    """

    async def _run() -> None:
        watcher = asyncio.create_task(_watch_stream(bot, client, registry))
        try:
            while True:
                for guild in list(bot.guilds):
                    try:
                        await ensure_live_dashboard(bot, str(guild.id), client, registry)
                    except Exception:
                        logger.warning(
                            "live dashboard refresh failed (guild %s) — best-effort", guild.id, exc_info=True
                        )
                await asyncio.sleep(DASHBOARD_REFRESH_INTERVAL_S)
        finally:
            watcher.cancel()

    return asyncio.create_task(_run())


PLATFORM = "discord"
DASHBOARD_REFRESH_INTERVAL_S = 15
DASHBOARD_MIN_EDIT_INTERVAL_S = 60
_LAST_PUSH: dict[str, tuple[float, str]] = {}


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Build a stable fingerprint of the rendered-relevant snapshot fields.

    ``generated_at`` changes on every stream frame; it is excluded —
    the fingerprint tracks the **player states** the dashboard renders,
    so an unchanged roster never triggers an edit (#251).
    """
    players = snapshot.get("players") or []
    return (
        "|".join(str(p.get("profile_id", "")) + ":" + str(p.get("state", "")) for p in players)
        + f"|degraded={snapshot.get('degraded', False)}"
    )


async def _watch_stream(
    bot: discord.Client,
    client: LiveClientLike,
    registry: MessageRegistryServiceLike,
) -> None:
    """Consume the WatchDashboard stream; every changed frame refreshes the dashboards."""
    from kingdoms.core.rpc.live import LiveClient

    if not isinstance(client, LiveClient):
        return
    while True:
        try:
            async for snapshot in client.stream_snapshots(GAME_KEY):
                await _push_snapshot(bot, client, registry, snapshot)
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
    stats = await collect_profile_stats([str(p.get("profile_id", "")) for p in snapshot.get("players", [])])
    body = render_dashboard(snapshot, stats=stats)
    embeds = _dashboard_embeds(snapshot, body, bot)
    for guild in list(bot.guilds):
        last_at, last_fp = _LAST_PUSH.get(str(guild.id), (0.0, ""))
        if fingerprint == last_fp:
            continue
        if now - last_at < DASHBOARD_MIN_EDIT_INTERVAL_S:
            continue
        _LAST_PUSH[str(guild.id)] = (now, fingerprint)
        channel = await ensure_live_dashboard_channel(guild)
        registered = await registry.resolve(PLATFORM, _page_key(1), str(guild.id))
        try:
            if not await _edit_registered(channel, registered, embeds[0]):
                await ensure_live_dashboard(bot, str(guild.id), client, registry)
            else:
                await _sync_extra_pages(str(guild.id), channel, registry, embeds)
        except Exception:
            logger.warning("live dashboard push failed (guild %s) — best-effort", guild.id, exc_info=True)
