"""The entity cards never cross Discord's component limits (#50035).

The 2026-11-10 merlin/test incident: a map card built from a long
Liquipedia Overview crossed Discord's 4000-char cap on displayable
component text, so every forum-post creation failed with 50035 — the
imports "succeeded" with zero posts. The card must degrade (clamp,
mark), never raise — and one failing post must never freeze the
forum sync pass.
"""

from __future__ import annotations

from typing import Any

import discord

from botforge.discord.ui.entity_cards import (
    MAX_DESCRIPTION_PARAGRAPHS,
    TEXT_BUDGET,
    EntityCardSpec,
    build_entity_card,
)


def test_a_long_description_stays_under_the_text_cap() -> None:
    description = "\n\n".join(f"Paragraph {i} " + "word " * 200 for i in range(6))
    spec = EntityCardSpec(
        title="Arabia",
        entity_id="map:aoe2:public:arabia",
        subtitle="Land",
        description=description,
        source_url="https://liquipedia.net/ageofempires/Arabia",
    )
    assert build_entity_card(spec).content_length() <= TEXT_BUDGET


def test_a_huge_description_stays_under_the_text_cap() -> None:
    description = "\n\n".join(f"Paragraph {i} " + "word " * 2000 for i in range(20))
    spec = EntityCardSpec(title="Arabia", entity_id="map:aoe2:public:arabia", description=description)
    assert build_entity_card(spec).content_length() <= TEXT_BUDGET


def test_many_paragraphs_never_cross_the_children_cap() -> None:
    description = "\n\n".join(f"Paragraph {i}" for i in range(200))
    view = build_entity_card(
        EntityCardSpec(title="Arabia", entity_id="map:aoe2:public:arabia", description=description)
    )
    assert len(view.children) <= 40
    assert view.content_length() <= TEXT_BUDGET


def test_a_clamped_card_marks_the_truncation() -> None:
    description = "\n\n".join(f"Paragraph {i} " + "word " * 100 for i in range(MAX_DESCRIPTION_PARAGRAPHS + 5))
    view = build_entity_card(
        EntityCardSpec(title="Arabia", entity_id="map:aoe2:public:arabia", description=description)
    )

    def _texts(node: Any) -> list[str]:
        out: list[str] = []
        text = getattr(node, "content", None)
        if isinstance(text, str):
            out.append(text)
        for child in getattr(node, "children", ()) or ():
            out.extend(_texts(child))
        return out

    joined = "\n".join(_texts(view))
    assert "description truncated" in joined, "a clamped card states that it was shortened"


def test_a_small_card_is_untouched() -> None:
    view = build_entity_card(
        EntityCardSpec(title="Arabia", entity_id="map:aoe2:public:arabia", description="The classic open map.")
    )
    assert "The classic open map." in str(view.to_components())


class _Entry:
    """An active map without its forum post (the creation shape)."""

    def __init__(self, map_id: str, name: str) -> None:
        self.id = map_id
        self.name = name
        self.game_key = "aoe2"
        self.description = "desc"
        self.resource_url = ""
        self.map_type = "Land"
        self.forum_message_id = ""
        self.archived_at = None
        self.owner_guild_id = None


class _Service:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries
        self.forum_messages: dict[str, str] = {}

    async def list_maps(self, game_key: str, guild_id: str | None = None) -> list[_Entry]:
        return self._entries

    async def list_all_map_pools(self, game_key: str, guild_id: str | None = None) -> list[Any]:
        return []

    async def set_map_forum_message(self, entry_id: str, forum_message_id: str) -> None:
        self.forum_messages[entry_id] = forum_message_id


class _Platform:
    """First post creation raises; the second must still run."""

    def __init__(self) -> None:
        self.created: list[str] = []
        self.fail_first = True

    async def forum_thread_exists(self, guild_id: str, thread_id: str) -> bool:
        return False

    async def create_map_post(
        self, guild_id: str, forum_id: str, name: str, content: str, tags: Any = None, view: Any = None
    ) -> str:
        if self.fail_first:
            self.fail_first = False
            raise discord.HTTPException(
                Any(), {"code": 50035, "message": "Invalid Form Body"}  # type: ignore[arg-type]
            )
        self.created.append(name)
        return f"thread-{len(self.created)}"

    async def edit_forum_post(self, guild_id: str, thread_id: str, content: str, view: Any) -> bool:
        return True


async def test_one_failing_post_never_freezes_the_forum_sync(monkeypatch: Any) -> None:
    import botforge.discord.channels_platform as cp
    import botforge.discord.maps_forum as mf
    import botforge.discord.ui.panels_provisioning as pp

    async def _stored(guild_id: str, scope: str, key: str) -> str | None:
        return "555"

    monkeypatch.setattr(pp, "stored_channel_id", _stored)

    class _Forum:
        type = discord.ChannelType.forum

    class _Guild:
        def get_channel(self, channel_id: int) -> Any:
            return _Forum()

    class _Bot:
        def get_guild(self, guild_id: int) -> Any:
            return _Guild()

    platform = _Platform()
    monkeypatch.setattr(cp, "DiscordChannelsPlatform", lambda bot: platform)

    async def _no_content(entity_id: str, guild_id: str, bot: Any) -> Any:
        return mf.MapPostContent()

    monkeypatch.setattr(mf, "_map_content_full", _no_content)

    service = _Service([_Entry("map:aoe2:public:big", "Big"), _Entry("map:aoe2:public:small", "Small")])
    created = await mf.sync_maps_forum("123", "aoe2", _Bot(), service)
    assert created == 1, "the map AFTER the failing one must still get its post"
    assert platform.created == ["Small"]
    assert service.forum_messages == {"map:aoe2:public:small": "thread-1"}
