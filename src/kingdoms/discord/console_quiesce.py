"""Guild quiesce: silence a guild's self-healing surfaces during a nuke.

The nuke deletes every channel and role of a test guild — but the
running bot keeps healing its surfaces while the deletion is in
progress: the live dashboard recreates its channel every refresh
interval (``ensure_live_dashboard_channel`` creates when missing), and
the boot provisioning re-creates the managed channels. A nuke without a
quiesce therefore leaves ``autohealed`` channels behind (the live
dashboard observed on the merlin env).

Contract: a destructive console action sets the quiesce marker first
(Redis, with a safety TTL so a crash never leaves a guild silenced
forever), deletes, then clears it. Every self-healing surface checks
``guild_quiesced`` before re-creating anything and skips quietly —
the channels stay deleted until the marker clears.

The marker lives in Redis (scope ``console``, key ``quiesce:<guild>``)
so it is shared by every process of the env and dies with the TTL even
if the bot crashes mid-nuke.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.discord.console_quiesce")

QUIESCE_SCOPE = "console"
QUIESCE_KEY_PREFIX = "quiesce"
QUIESCE_TTL_SECONDS = 300

__all__ = [
    "guild_quiesced",
    "quiesce_guild",
    "release_guild",
]


def _state(interaction_or_bot: Any) -> Any | None:
    """Resolve the shared StateService from a bot or an interaction's client."""
    bot = getattr(interaction_or_bot, "client", None) or interaction_or_bot
    return getattr(bot, "state_service", None)


def _key(guild_id: str | int | None) -> str:
    return f"{QUIESCE_KEY_PREFIX}:{guild_id}"


async def quiesce_guild(bot: Any, guild_id: str | int | None) -> None:
    """Set the guild's quiesce marker (idempotent, safety TTL)."""
    state = _state(bot)
    if state is None:
        return
    try:
        await state.set_state(
            QUIESCE_SCOPE,
            _key(guild_id),
            {"quiesced": True},
            ttl=QUIESCE_TTL_SECONDS,
        )
        logger.warning("CONSOLE quiesce set (guild %s) — self-healing silenced", guild_id)
    except Exception:
        logger.warning("CONSOLE quiesce set failed (guild %s) — best-effort", guild_id, exc_info=True)


async def release_guild(bot: Any, guild_id: str | int | None) -> None:
    """Clear the guild's quiesce marker (the guild heals again)."""
    state = _state(bot)
    if state is None:
        return
    try:
        await state.delete_state(QUIESCE_SCOPE, _key(guild_id))
        logger.info("CONSOLE quiesce cleared (guild %s) — self-healing resumed", guild_id)
    except Exception:
        logger.warning("CONSOLE quiesce clear failed (guild %s) — best-effort", guild_id, exc_info=True)


async def guild_quiesced(bot: Any, guild_id: str | int | None) -> bool:
    """Whether the guild is quiesced (a destructive action is in flight)."""
    state = _state(bot)
    if state is None:
        return False
    try:
        entry = await state.get_state(QUIESCE_SCOPE, _key(guild_id))
    except Exception:
        return False
    return bool((entry or {}).get("quiesced"))


async def mark_guild_nuked(guild_id: str | int | None) -> None:
    """Persist the nuked flag: the guild's self-healing surfaces stay off.

    Unlike the quiesce marker (a short-lived TTL lock held during a
    destructive pass), the nuked flag is durable guild state: after a
    nuke the guild is expected to stay EMPTY — the live dashboard must
    not recreate its channel 15 seconds later. The flag lives in the
    guild settings (Mongo, ``guild_settings``) so it survives restarts
    and deployments; ``clear_guild_nuked`` turns the surfaces back on
    (a re-provision through /admin, an install, or a reset).
    """
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.logs import GUILD_SETTINGS_COLLECTION

    database = get_async_database()
    await database[GUILD_SETTINGS_COLLECTION].update_one(
        {"_id": str(guild_id)},
        {"$set": {"console_nuked": True}},
        upsert=True,
    )
    logger.warning("CONSOLE nuked flag set (guild %s) — self-healing stays off until re-provision", guild_id)


async def clear_guild_nuked(guild_id: str | int | None) -> None:
    """Clear the nuked flag: the guild's self-healing surfaces resume."""
    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.logs import GUILD_SETTINGS_COLLECTION

    database = get_async_database()
    await database[GUILD_SETTINGS_COLLECTION].update_one(
        {"_id": str(guild_id)},
        {"$set": {"console_nuked": False}},
        upsert=True,
    )
    logger.info("CONSOLE nuked flag cleared (guild %s) — self-healing resumes", guild_id)


async def guild_nuked(guild_id: str | int | None) -> bool:
    """Whether the guild was nuked and not yet re-provisioned."""
    if guild_id is None:
        return False
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.logs import GUILD_SETTINGS_COLLECTION

        database = get_async_database()
        doc = await database[GUILD_SETTINGS_COLLECTION].find_one({"_id": str(guild_id)})
        return bool((doc or {}).get("console_nuked"))
    except Exception:
        logger.warning("CONSOLE nuked flag read failed (guild %s) — assuming not nuked", guild_id)
        return False
