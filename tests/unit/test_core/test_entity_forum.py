"""Generic entity-forum engine — unit tests (kingdoms.core.services.entity_forum).

The stored-id contract: the spec groups its entities by (tree scope,
tree channel key); each forum is resolved by the channel id the
provisioning stored — never by its translated name. Nothing stored,
nothing synced: the provisioning owns creation.
"""

from __future__ import annotations

from typing import Any

import discord

from kingdoms.core.services.entity_forum import EntityForumSpec, sync_entity_forum


class _Faction:
    """A core-shaped faction entity."""

    def __init__(self, game_key: str, name: str, entry_id: str) -> None:
        self.game_key = game_key
        self.name = name
        self.id = entry_id


class _Thread:
    """A forum thread stand-in."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.id = 42
        self.edited = False

    async def fetch_message(self, thread_id: str) -> Any:
        del thread_id

        class _Msg:
            async def edit(self, content: str, view: Any = None) -> None:
                del content, view

        return _Msg()


class _Channel:
    """A guild channel stand-in (forum-typed when asked)."""

    def __init__(self, channel_id: int, forum: bool = True) -> None:
        self.id = channel_id
        self.type = discord.ChannelType.forum if forum else discord.ChannelType.text
        self.threads: list[_Thread] = []


class _Guild:
    """A guild stand-in resolving channels by id."""

    def __init__(self, channels: dict[int, _Channel] | None = None) -> None:
        self._channels = channels or {}

    def get_channel(self, channel_id: int) -> _Channel | None:
        return self._channels.get(channel_id)


class _Bot:
    """Bot stand-in resolving its single guild."""

    def __init__(self, guild: _Guild) -> None:
        self._guild = guild

    def get_guild(self, guild_id: int) -> _Guild:
        del guild_id
        return self._guild


class _Platform:
    """Channels-platform stand-in recording the created posts."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []

    async def create_map_post(self, guild_id: str, forum_id: str, name: str, content: str, view: Any = None) -> str:
        del guild_id, forum_id, view
        self.posts.append((name, content))
        return "thread-1"


def _spec(factions: list[_Faction], stored: dict[str, str]) -> EntityForumSpec:
    async def list_factions(guild_id: str) -> list[_Faction]:
        del guild_id
        return factions

    async def build_post(faction: _Faction, guild_id: str) -> tuple[str, None]:
        del guild_id
        return f"**{faction.name}**", None

    return EntityForumSpec(
        list_entities=list_factions,
        build_post=build_post,
        tree_scope_for=lambda faction: f"games/{faction.game_key}",
        tree_channel_for=lambda faction: "civs",
    )


def _wire(monkeypatch: Any, guild: _Guild, platform: _Platform, stored: dict[str, str]) -> None:
    monkeypatch.setattr("kingdoms.discord.channels_platform.DiscordChannelsPlatform", lambda b: platform)
    monkeypatch.setattr(
        "kingdoms.discord.ui.panels_provisioning.stored_channel_id",
        lambda guild_id, scope, key: _async_value(stored.get(f"{scope}:{key}")),
    )


def _async_value(value: str | None) -> Any:
    class _Awaitable:
        def __await__(self):
            if False:  # pragma: no cover — keeps this a generator
                yield
            return value

    return _Awaitable()


async def test_the_forum_resolves_by_its_stored_id(monkeypatch: Any) -> None:
    forum = _Channel(101)
    guild = _Guild({101: forum})
    bot = _Bot(guild)
    platform = _Platform()
    _wire(monkeypatch, guild, platform, {"games/aoe2:civs": "101"})
    factions = [_Faction("aoe2", "Britons", "faction:aoe2:britons")]

    actions = await sync_entity_forum("1", bot, _spec(factions, {}))

    assert actions == 1
    assert [name for name, _ in platform.posts] == ["Britons"]


async def test_nothing_stored_means_nothing_synced(monkeypatch: Any) -> None:
    guild = _Guild({101: _Channel(101)})
    bot = _Bot(guild)
    platform = _Platform()
    _wire(monkeypatch, guild, platform, {})
    factions = [_Faction("aoe2", "Britons", "faction:aoe2:britons")]

    actions = await sync_entity_forum("1", bot, _spec(factions, {}))

    assert actions == 0
    assert platform.posts == []


async def test_a_stale_stored_id_creates_nothing(monkeypatch: Any) -> None:
    guild = _Guild()  # channel 404 was deleted
    bot = _Bot(guild)
    platform = _Platform()
    _wire(monkeypatch, guild, platform, {"games/aoe2:civs": "404"})
    factions = [_Faction("aoe2", "Britons", "faction:aoe2:britons")]

    actions = await sync_entity_forum("1", bot, _spec(factions, {}))

    assert actions == 0
    assert platform.posts == []


async def test_existing_threads_are_left_alone(monkeypatch: Any) -> None:
    forum = _Channel(101)
    forum.threads.append(_Thread("Britons"))
    guild = _Guild({101: forum})
    bot = _Bot(guild)
    platform = _Platform()
    _wire(monkeypatch, guild, platform, {"games/aoe2:civs": "101"})
    factions = [_Faction("aoe2", "Britons", "faction:aoe2:britons")]

    actions = await sync_entity_forum("1", bot, _spec(factions, {}))

    assert actions == 0
    assert platform.posts == []
