"""Entity-post content resolution: locale-aware, Redis then Mongo (core).

The forums' posts (maps, factions) carry localized content seeded from
a licence-compatible provider (see ``kingdoms.core.services.faction_content``).
This module resolves, at post build time, the guild's content for one
entity: the guild's locale (en fallback), then the content through the
cache-aside service — Redis first, Mongo second. When no content is
stored the entity's catalog name stands in and the summary is empty,
so a fresh environment still gets its posts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("kingdoms.discord.content_posts")

_CONTENT_SERVICE: Any = None
_CONTENT_READY = False


def _build_content_service() -> Any | None:
    """Build the FactionContentService over the shared Mongo adapter."""
    import os

    from kingdoms.core.services.faction_content import FactionContentService

    if not os.environ.get("MONGO_URI"):
        return None
    try:
        from kingdoms.core.models.db import get_async_database

        return FactionContentService(get_async_database())
    except Exception:
        logger.warning("content service unavailable", exc_info=True)
        return None


def content_service() -> Any | None:
    """Memoize the content service (None when Mongo is absent)."""
    global _CONTENT_SERVICE, _CONTENT_READY
    if _CONTENT_READY:
        return _CONTENT_SERVICE
    _CONTENT_READY = True
    _CONTENT_SERVICE = _build_content_service()
    return _CONTENT_SERVICE


async def guild_locale(guild_id: str, bot: Any) -> str:
    """Resolve the guild's configured locale (en fallback)."""
    logs = getattr(bot, "logs_service", None)
    if logs is None or not guild_id:
        return "en"
    try:
        return str(await logs.get_locale(guild_id))
    except Exception:
        logger.debug("locale read failed (guild %s)", guild_id, exc_info=True)
        return "en"


async def entity_post_content(entity_id: str, fallback_name: str, guild_id: str, bot: Any) -> tuple[str, str, str, str]:
    """Return ``(name, summary, source_url, image_url)`` for one entity's post.

    The stored content wins; the catalog entry's name is the fallback.
    """
    service = content_service()
    locale = await guild_locale(guild_id, bot)
    if service is None:
        return fallback_name, "", "", ""
    try:
        doc = await service.get(entity_id, locale)
    except Exception:
        logger.debug("content read failed (%s)", entity_id, exc_info=True)
        doc = None
    if doc is None:
        return fallback_name, "", "", ""
    return (
        str(doc.get("name") or fallback_name),
        str(doc.get("summary") or ""),
        str(doc.get("source_url") or ""),
        str(doc.get("image_url") or ""),
    )
