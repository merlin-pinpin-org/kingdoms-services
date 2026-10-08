"""Generic entity-forum engine — unit tests (kingdoms.core.services.entity_forum)."""

from __future__ import annotations

from typing import Any

from kingdoms.core.services.entity_forum import EntityForumSpec, sync_entity_forum


class _Civ:
    """A core-shaped civ entity."""

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


class _Forum:
    """A guild forum stand-in with its threads."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.id = 7
        self.threads: list[_Thread] = []


class _Guild:
    """A guild stand-in with its forums (and a ``games`` category)."""

    def __init__(self) -> None:
        self.forums: list[_Forum] = []
        self.created: list[str] = []

        class _Category:
            name = "games"

        self.categories = [_Category()]
        self.default_role = object()
        self.me = object()

    async def create_category(self, name: str, reason: str = "") -> Any:
        del name, reason
        return None

    async def create_forum(self, name: str, **kwargs: Any) -> _Forum:
        del kwargs
        self.created.append(name)
        forum = _Forum(name)
        self.forums.append(forum)
        return forum


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


def _spec(bot: _Bot, platform: _Platform, civs: list[_Civ], *, per_game: bool) -> EntityForumSpec:
    async def list_civs(guild_id: str) -> list[_Civ]:
        del guild_id
        return civs

    async def build_post(civ: _Civ, guild_id: str) -> tuple[str, None]:
        del guild_id
        return f"**{civ.name}**", None

    return EntityForumSpec(
        forum_name="" if per_game else "aoe2-civs",
        list_entities=list_civs,
        build_post=build_post,
        forum_name_for=(lambda civ: f"{civ.game_key}-civs") if per_game else None,
    )


async def test_sync_groups_entities_into_one_forum_per_game(monkeypatch: Any) -> None:
    guild = _Guild()
    bot = _Bot(guild)
    platform = _Platform()
    monkeypatch.setattr("kingdoms.discord.channels_platform.DiscordChannelsPlatform", lambda b: platform)
    civs = [_Civ("aoe2", "Britons", "civ:aoe2:britons"), _Civ("aoe2", "Franks", "civ:aoe2:franks")]
    actions = await sync_entity_forum("1", bot, _spec(bot, platform, civs, per_game=True))
    assert guild.created == ["aoe2-civs"]
    assert [name for name, _ in platform.posts] == ["Britons", "Franks"]
    assert actions == 2


async def test_sync_uses_the_fixed_forum_name_without_forum_name_for(monkeypatch: Any) -> None:
    guild = _Guild()
    bot = _Bot(guild)
    platform = _Platform()
    monkeypatch.setattr("kingdoms.discord.channels_platform.DiscordChannelsPlatform", lambda b: platform)
    civs = [_Civ("aoe2", "Britons", "civ:aoe2:britons")]
    actions = await sync_entity_forum("1", bot, _spec(bot, platform, civs, per_game=False))
    assert guild.created == ["aoe2-civs"]
    assert actions == 1


async def test_sync_is_idempotent_for_existing_threads(monkeypatch: Any) -> None:
    guild = _Guild()
    forum = _Forum("aoe2-civs")
    forum.threads.append(_Thread("Britons"))
    guild.forums.append(forum)
    bot = _Bot(guild)
    platform = _Platform()
    monkeypatch.setattr("kingdoms.discord.channels_platform.DiscordChannelsPlatform", lambda b: platform)
    civs = [_Civ("aoe2", "Britons", "civ:aoe2:britons")]
    actions = await sync_entity_forum("1", bot, _spec(bot, platform, civs, per_game=False))
    assert actions == 0
    assert platform.posts == []
