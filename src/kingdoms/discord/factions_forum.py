"""The factions forum: one post per faction, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-factions`` forum (e.g. ``aoe2-factions``). Every
non-archived faction gets exactly one post — the faction's public
surface and the future link target for mod-specific faction entities
(the kingdoms mod overlays its own concepts on AoE2 civilizations and
will link to these posts).

The posts are created **without content** on purpose: the goal today
is the forum and its stable link targets, not the description. The
sync is idempotent and self-healing — a deleted post is recreated, an
archived faction's post disappears — and runs on the core
entity-forum engine, so no game or mod is hardcoded here.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.entity_forum import EntityForumSpec

FACTIONS_FORUM_SUFFIX = "-factions"


def factions_forum_name(game_key: str) -> str:
    """Build the per-game factions forum name (``aoe2`` -> ``aoe2-factions``)."""
    return f"{game_key}{FACTIONS_FORUM_SUFFIX}"


def _game_data(bot: Any) -> Any | None:
    from kingdoms.discord.wiring import build_games_service

    try:
        return build_games_service()
    except Exception:
        return None


def factions_forum_spec(bot: Any) -> EntityForumSpec:
    """Build the factions forum spec wired onto the games service."""

    async def list_factions(guild_id: str) -> list[Any]:
        del guild_id
        service = _game_data(bot)
        if service is None:
            return []
        factions: list[Any] = []
        for game_key in await service.list_game_keys():
            factions.extend(await service.list_factions(game_key))
        return factions

    async def build_post(faction: Any, guild_id: str) -> tuple[str, Any | None]:
        from kingdoms.core.ids import footer
        from kingdoms.discord.content_posts import entity_post_content

        entry_id = str(getattr(faction, "id", ""))
        fallback = str(getattr(faction, "name", faction))
        name, summary, source = await entity_post_content(entry_id, fallback, guild_id, bot)
        head = f"Source : {source}\n" if source else ""
        content = f"{head}**{name}**\n{summary}\n\n{footer(entry_id)}"
        return content, None

    def forum_name_for(faction: Any) -> str:
        return factions_forum_name(str(getattr(faction, "game_key", "")))

    return EntityForumSpec(
        forum_name="",
        list_entities=list_factions,
        build_post=build_post,
        forum_name_for=forum_name_for,
    )
