"""The civs forum: one post per civilization, per game (core).

Every game known to the catalog gets, in the ``games`` category, a
``<game>-civs`` forum (e.g. ``aoe2-civs``). Every non-archived civ
gets exactly one post — the civ's public surface and the future link
target for mod-specific civilization entities (the kingdoms mod
overlays its own concepts on AoE2 civs and will link to these posts).

The posts are created **without content** on purpose: the goal today
is the forum and its stable link targets, not the description. The
sync is idempotent and self-healing — a deleted post is recreated, an
archived civ's post disappears — and runs on the core entity-forum
engine, so no game or mod is hardcoded here.
"""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.entity_forum import EntityForumSpec

CIVS_FORUM_SUFFIX = "-civs"


def civs_forum_name(game_key: str) -> str:
    """Build the per-game civs forum name (``aoe2`` -> ``aoe2-civs``)."""
    return f"{game_key}{CIVS_FORUM_SUFFIX}"


def _game_data(bot: Any) -> Any | None:
    from kingdoms.discord.wiring import build_games_service

    try:
        return build_games_service()
    except Exception:
        return None


def civs_forum_spec(bot: Any) -> EntityForumSpec:
    """Build the civs forum spec wired onto the games service."""

    async def list_civs(guild_id: str) -> list[Any]:
        del guild_id
        service = _game_data(bot)
        if service is None:
            return []
        civs: list[Any] = []
        for game_key in await service.list_game_keys():
            civs.extend(await service.list_civs(game_key))
        return civs

    async def build_post(civ: Any, guild_id: str) -> tuple[str, Any | None]:
        del guild_id
        from kingdoms.core.ids import footer

        name = str(getattr(civ, "name", civ))
        entry_id = str(getattr(civ, "id", ""))
        content = f"**{name}**\n\n{footer(entry_id)}"
        return content, None

    def forum_name_for(civ: Any) -> str:
        return civs_forum_name(str(getattr(civ, "game_key", "")))

    return EntityForumSpec(
        forum_name="",
        list_entities=list_civs,
        build_post=build_post,
        forum_name_for=forum_name_for,
    )
