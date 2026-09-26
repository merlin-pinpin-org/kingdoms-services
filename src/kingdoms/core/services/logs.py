"""LogService: lifecycle events in the per-guild bot logs channel (#109).

Resolves each guild's ``🤖-bot-logs`` channel through the cache-aside
flow (kingdoms-services#23): Redis (``kingdoms:channels:{guild}:bot_logs``)
→ MongoDB (``channels``, :class:`~kingdoms.core.models.channel.ChannelModel`)
→ platform creation. The channel is **admin-only by default**: view is
granted to guild administrators and BOT_ADMINS, denied to @everyone —
applied atomically at creation so the channel is never public, even
briefly.

Access policies are per-guild, persisted in MongoDB, and manageable
through ``/admin`` (re-apply default, grant/revoke a role view access);
every policy change is itself logged as an audit event.

Logged events: start/stop/restart, crash, status change, policy change
— each localized (en/fr); the "start" event is the deployment
announcement carrying the frozen ``kingdoms-deploy`` footer
(kingdoms-services#52, read back by the kingdoms-infra battery #78).

Reliability: best-effort end to end. Redis down → fall back to MongoDB;
both down → skip the event. A deleted channel is detected on send
failure, dropped from the caches, and re-provisioned on the next event.
Crash-loop guard: repeated start/stop within a short window collapses
into one "crash-loop detected" event with a counter.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from kingdoms.core.enums.channel_category import ChannelCategory
from kingdoms.core.models.channel import ChannelModel

logger = logging.getLogger("kingdoms.logs")

BOT_LOGS_CATEGORY = str(ChannelCategory.BOT_LOGS)
BOT_LOGS_CHANNEL_NAME = "🤖-bot-logs"
CHANNELS_COLLECTION = "channels"
POLICIES_COLLECTION = "channel_access_policies"
GUILD_SETTINGS_COLLECTION = "guild_settings"
CACHE_TTL_SECONDS = 3600
CRASH_LOOP_WINDOW_SECONDS = 300
CRASH_LOOP_THRESHOLD = 3


class LogsDatabase(Protocol):
    """Narrow async MongoDB seam the LogService depends on."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database."""
        ...

    async def find_channel(self, guild_id: str, category: str) -> ChannelModel | None:
        """Find the persisted channel document for a guild category."""
        ...

    async def upsert_channel(self, channel: ChannelModel) -> None:
        """Insert or replace the channel document."""
        ...

    async def delete_channel(self, guild_id: str, category: str) -> bool:
        """Drop a channel document; True when one was removed."""
        ...

    async def get_policy(self, guild_id: str, category: str) -> dict[str, Any] | None:
        """Read the persisted access policy for a guild category."""
        ...

    async def set_policy(self, guild_id: str, category: str, policy: dict[str, Any]) -> None:
        """Persist the access policy (upsert)."""
        ...

    async def get_guild_settings(self, guild_id: str) -> dict[str, Any] | None:
        """Read the per-guild settings (locale, ...)."""
        ...

    async def set_guild_settings(self, guild_id: str, settings: dict[str, Any]) -> None:
        """Persist the per-guild settings (upsert)."""
        ...


class LogsPlatform(Protocol):
    """Narrow platform seam: creation, permissions, sending."""

    async def find_logs_channel(self, guild_id: str) -> str | None:
        """Find an existing logs channel by name; None when there is none."""
        ...

    async def create_logs_channel(self, guild_id: str) -> str:
        """Create the logs channel; return its id."""
        ...

    async def apply_default_policy(self, guild_id: str, channel_id: str) -> None:
        """Apply the admin-only default permission overwrites."""
        ...

    async def apply_public_policy(self, guild_id: str, channel_id: str) -> None:
        """Open the channel to everyone (public visibility)."""
        ...

    async def grant_role_view(self, guild_id: str, channel_id: str, role_id: str) -> None:
        """Grant a role view access on the logs channel."""
        ...

    async def send_log_message(self, guild_id: str, channel_id: str, content: str, layout: Any = None) -> None:
        """Deliver one lifecycle event to the logs channel (layout optional)."""
        ...

    async def channel_exists(self, guild_id: str, channel_id: str) -> bool:
        """Whether the channel still exists on the platform."""
        ...

    async def list_text_channels(self, guild_id: str) -> list[dict[str, str]]:
        """List the guild's text channels (id, name) for the admin picker."""
        ...


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    """One bot lifecycle event, ready to render and send.

    ``layout`` is opaque to the core (platform-typed: a discord.py
    Components V2 ``LayoutView`` on the Discord platform) — the
    platform seam renders it, the core only carries it. ``message`` is
    the plain-text rendering, sent when no layout is provided.
    """

    kind: str
    message: str
    footer: str = ""
    layout: Any = None


def default_policy() -> dict[str, Any]:
    """Build the default access policy: admin-only (guild admins + BOT_ADMINS)."""
    return {
        "default": "admin_only",
        "roles_with_view": [],
        "updated_at": int(time.time()),
    }


class LogService:
    """Resolve the bot logs channel and deliver lifecycle events to it."""

    def __init__(self, database: LogsDatabase, platform: LogsPlatform, state: Any, clock: Any = time.monotonic) -> None:
        """Wire the stores; ``state`` is a StateService (Redis cache-aside)."""
        self._db = database
        self._platform = platform
        self._state = state
        self._clock = clock

    async def resolve_channel(self, guild_id: str) -> str | None:
        """Cache-aside resolution: Redis → MongoDB → creation (admin-only)."""
        cache_key = self._cache_key(guild_id)
        cached = await self._safe(self._state.get_state("channels", cache_key))
        if cached and cached.get("channel_id"):
            if await self._platform.channel_exists(guild_id, str(cached["channel_id"])):
                return str(cached["channel_id"])
            await self._drop_caches(guild_id)
        stored = await self._db.find_channel(guild_id, BOT_LOGS_CATEGORY)
        if stored is not None:
            if await self._platform.channel_exists(guild_id, stored.channel_id):
                await self._cache(guild_id, stored.channel_id)
                return stored.channel_id
            await self._db.delete_channel(guild_id, BOT_LOGS_CATEGORY)
        adopted = await self._safe_find_channel(guild_id)
        if adopted is not None:
            await self._db.upsert_channel(
                ChannelModel(
                    _id=f"{guild_id}:{BOT_LOGS_CATEGORY}",
                    guild_id=guild_id,
                    platform="discord",
                    category=BOT_LOGS_CATEGORY,
                    channel_id=adopted,
                    name=BOT_LOGS_CHANNEL_NAME,
                )
            )
            await self._cache(guild_id, adopted)
            return adopted
        channel_id = await self._platform.create_logs_channel(guild_id)
        await self._platform.apply_default_policy(guild_id, channel_id)
        await self._db.set_policy(guild_id, BOT_LOGS_CATEGORY, default_policy())
        await self._db.upsert_channel(
            ChannelModel(
                _id=f"{guild_id}:{BOT_LOGS_CATEGORY}",
                guild_id=guild_id,
                platform="discord",
                category=BOT_LOGS_CATEGORY,
                channel_id=channel_id,
                name=BOT_LOGS_CHANNEL_NAME,
            )
        )
        await self._cache(guild_id, channel_id)
        return channel_id

    async def log_event(self, guild_id: str, event: LifecycleEvent) -> None:
        """Deliver one lifecycle event to the guild's logs channel (best-effort)."""
        try:
            channel_id = await self.resolve_channel(guild_id)
            if channel_id is None:
                return
            content = event.message if not event.footer else f"{event.message}\n-# {event.footer}"
            await self._platform.send_log_message(guild_id, channel_id, content, layout=event.layout)
        except Exception:
            logger.warning("LIFECYCLE LOG DELIVERY FAILED (guild %s, event %s) — best-effort", guild_id, event.kind)

    async def log_crash_loop(self, guild_id: str, base_event: LifecycleEvent) -> None:
        """Collapse repeated start/stop into one crash-loop event with a counter."""
        key = f"crash_loop:{guild_id}"
        state = await self._safe(self._state.get_state("logs", key))
        count = int(state["count"]) + 1 if state else 1
        await self._safe(self._state.set_state("logs", key, {"count": count}, ttl=CRASH_LOOP_WINDOW_SECONDS))
        if count < CRASH_LOOP_THRESHOLD:
            return
        minutes = CRASH_LOOP_WINDOW_SECONDS // 60
        event = LifecycleEvent(
            kind="crash_loop",
            message=f"{base_event.message} (crash-loop detected: {count} events in {minutes} min)",
            footer=base_event.footer,
        )
        await self.log_event(guild_id, event)

    async def get_access_policy(self, guild_id: str) -> dict[str, Any]:
        """Read the guild's logs access policy (default: admin-only)."""
        return await self._db.get_policy(guild_id, BOT_LOGS_CATEGORY) or default_policy()

    async def grant_role_view_access(self, guild_id: str, role_id: str, by: str) -> None:
        """Grant a role view access on the logs channel; audited as an event."""
        policy = await self.get_access_policy(guild_id)
        roles = [r for r in policy.get("roles_with_view", []) if r != role_id]
        roles.append(role_id)
        policy["roles_with_view"] = roles
        policy["updated_at"] = int(time.time())
        await self._db.set_policy(guild_id, BOT_LOGS_CATEGORY, policy)
        channel_id = await self.resolve_channel(guild_id)
        if channel_id:
            await self._platform.grant_role_view(guild_id, channel_id, role_id)
        await self.log_event(
            guild_id,
            LifecycleEvent(
                kind="policy", message=f"Access policy updated: role <@&{role_id}> granted view, by <@{by}>."
            ),
        )

    async def reapply_default_policy(self, guild_id: str, by: str) -> None:
        """Reset the logs channel to the default admin-only policy; audited."""
        await self._db.set_policy(guild_id, BOT_LOGS_CATEGORY, default_policy())
        channel_id = await self.resolve_channel(guild_id)
        if channel_id:
            await self._platform.apply_default_policy(guild_id, channel_id)
        await self.log_event(
            guild_id,
            LifecycleEvent(kind="policy", message=f"Access policy reset to admin-only, by <@{by}>."),
        )

    async def set_channel(self, guild_id: str, channel_id: str, by: str) -> None:
        """Route the bot logs to an existing channel; audited as an event.

        The channel must already exist on the platform (the admin picks
        it from the guild's channels); the current visibility policy is
        re-applied on the new target so the rights follow the routing.
        """
        if not await self._platform.channel_exists(guild_id, channel_id):
            raise ValueError(f"channel {channel_id} does not exist in guild {guild_id}")
        policy = await self.get_access_policy(guild_id)
        previous = await self._db.find_channel(guild_id, BOT_LOGS_CATEGORY)
        await self._db.upsert_channel(
            ChannelModel(
                _id=f"{guild_id}:{BOT_LOGS_CATEGORY}",
                guild_id=guild_id,
                platform="discord",
                category=BOT_LOGS_CATEGORY,
                channel_id=channel_id,
                name=BOT_LOGS_CHANNEL_NAME,
            )
        )
        await self._cache(guild_id, channel_id)
        if policy.get("default") == "public":
            await self._platform.apply_public_policy(guild_id, channel_id)
        else:
            await self._platform.apply_default_policy(guild_id, channel_id)
        for role_id in policy.get("roles_with_view", []):
            await self._platform.grant_role_view(guild_id, channel_id, str(role_id))
        previous_note = f" (was <#{previous.channel_id}>)" if previous is not None else ""
        await self.log_event(
            guild_id,
            LifecycleEvent(
                kind="policy", message=f"Bot logs routed to <#{channel_id}>{previous_note}, by <@{by}>."
            ),
        )

    async def set_visibility(self, guild_id: str, public: bool, by: str) -> None:
        """Set the logs channel visibility (public/admin-only); audited.

        The policy is persisted first, the platform overwrites second —
        a crash in between leaves the channel more restrictive than the
        record, never the reverse.
        """
        policy = await self.get_access_policy(guild_id)
        policy["default"] = "public" if public else "admin_only"
        policy["updated_at"] = int(time.time())
        await self._db.set_policy(guild_id, BOT_LOGS_CATEGORY, policy)
        channel_id = await self.resolve_channel(guild_id)
        if channel_id:
            if public:
                await self._platform.apply_public_policy(guild_id, channel_id)
            else:
                await self._platform.apply_default_policy(guild_id, channel_id)
                for role_id in policy.get("roles_with_view", []):
                    await self._platform.grant_role_view(guild_id, channel_id, str(role_id))
        state = "public" if public else "admin-only"
        await self.log_event(
            guild_id,
            LifecycleEvent(kind="policy", message=f"Bot logs visibility set to {state}, by <@{by}>."),
        )

    async def list_text_channels(self, guild_id: str) -> list[dict[str, str]]:
        """List the guild's text channels (id, name) for the admin picker."""
        return await self._platform.list_text_channels(guild_id)

    async def get_locale(self, guild_id: str) -> str:
        """Read the guild's locale (en fallback)."""
        settings = await self._safe(self._db.get_guild_settings(guild_id))
        locale = str((settings or {}).get("locale", ""))
        return locale if locale in ("en", "fr") else "en"

    async def set_locale(self, guild_id: str, locale: str, by: str) -> None:
        """Persist the guild's locale (fr/en); audited as an event."""
        if locale not in ("en", "fr"):
            raise ValueError(f"unsupported locale: {locale!r}")
        settings = await self._safe(self._db.get_guild_settings(guild_id)) or {}
        settings["locale"] = locale
        settings["updated_at"] = int(time.time())
        settings["updated_by"] = by
        await self._db.set_guild_settings(guild_id, settings)
        await self.log_event(
            guild_id,
            LifecycleEvent(kind="policy", message=f"Language set to {locale}, by <@{by}>."),
        )

    async def _cache(self, guild_id: str, channel_id: str) -> None:
        await self._safe(
            self._state.set_state(
                "channels", self._cache_key(guild_id), {"channel_id": channel_id}, ttl=CACHE_TTL_SECONDS
            )
        )

    async def _safe_find_channel(self, guild_id: str) -> str | None:
        """Find an existing logs channel by name, degrading to None on failure."""
        try:
            return await self._platform.find_logs_channel(guild_id)
        except Exception:
            return None

    async def _drop_caches(self, guild_id: str) -> None:
        await self._safe(self._state.delete_state("channels", self._cache_key(guild_id)))
        await self._db.delete_channel(guild_id, BOT_LOGS_CATEGORY)

    def _cache_key(self, guild_id: str) -> str:
        return f"{guild_id}:{BOT_LOGS_CATEGORY}"

    async def _safe(self, awaitable: Any) -> Any:
        """Await a store call, degrading to None on failure (Redis down)."""
        try:
            return await awaitable
        except Exception:
            return None
