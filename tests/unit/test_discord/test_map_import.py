"""#259: the Liquipedia map import is rollback-safe.

A failed import must leave no residue: the record rolls back when the
post-create tail (content store, forum sync) fails; when the rollback
itself fails, the popup says the map is orphaned and how to clean it.
"""

from __future__ import annotations

from typing import Any


class _FailingForumSync:
    """Monkeypatch target: the forum sync step raises."""

    async def __call__(self, guild_id: str, game_key: str, client: Any) -> None:
        raise RuntimeError("forum down")


class _Seed:
    name = "Arabia"
    description = "The classic open map"
    resource_url = "https://liquipedia.net/ageofempires/Arabia"
    image_url = "https://liquipedia.net/commons/arabia.png"
    map_type = "Land"


class _Entry:
    id = "map:aoe2:public:arabia"
    name = "Arabia"
    forum_message_id = None


class _FakeGamesService:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    async def create_map(self, game_key: str, name: str, **kwargs: Any) -> _Entry:
        self.created.append(name)
        return _Entry()

    async def delete_map(self, entry_id: str) -> bool:
        self.deleted.append(entry_id)
        return True

    async def get_map(self, entry_id: str) -> _Entry | None:
        return _Entry() if entry_id == _Entry.id else None


class _Followups:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> None:
        self.messages.append(content or "")


class _Response:
    deferred = False

    async def defer(self, ephemeral: bool = True, thinking: bool = True) -> None:
        self.deferred = True


class _Interaction:
    guild_id = "123"
    client = None

    def __init__(self) -> None:
        self.followup = _Followups()
        self.response = _Response()


async def _fake_seed(name: str) -> _Seed:
    return _Seed()


async def _run_import(interaction: _Interaction) -> None:
    from kingdoms.discord.admin_panel_games import GamesMapImportModal

    modal = GamesMapImportModal("aoe2")
    object.__setattr__(modal.name, "_value", "Arabia")
    await modal.on_submit(interaction)


async def test_import_success_confirms(monkeypatch: Any) -> None:
    import kingdoms.discord.admin_panel_games as panel

    service = _FakeGamesService()
    interaction = _Interaction()
    monkeypatch.setattr(panel, "_games_wiring", lambda: service)
    monkeypatch.setattr("kingdoms.mapsdata.seed.fetch_map_seed", _fake_seed)

    async def ok_sync(guild_id: str, game_key: str, client: Any) -> None:
        return None

    monkeypatch.setattr("kingdoms.discord.maps_forum.sync_maps_forum", ok_sync)
    await _run_import(interaction)
    assert service.created == ["Arabia"]
    assert service.deleted == []
    assert "importée" in interaction.followup.messages[-1]


async def test_import_rolls_back_on_forum_failure(monkeypatch: Any) -> None:
    import kingdoms.discord.admin_panel_games as panel

    service = _FakeGamesService()
    interaction = _Interaction()
    monkeypatch.setattr(panel, "_games_wiring", lambda: service)
    monkeypatch.setattr("kingdoms.mapsdata.seed.fetch_map_seed", _fake_seed)
    monkeypatch.setattr(
        "kingdoms.discord.maps_forum.sync_maps_forum", _FailingForumSync()
    )
    await _run_import(interaction)
    assert "supprimée" in interaction.followup.messages[-1]
    assert service.deleted == ["map:aoe2:public:arabia"]


async def test_import_orphan_message_when_rollback_fails(monkeypatch: Any) -> None:
    import kingdoms.discord.admin_panel_games as panel

    service = _FakeGamesService()
    interaction = _Interaction()
    monkeypatch.setattr(panel, "_games_wiring", lambda: service)
    monkeypatch.setattr("kingdoms.mapsdata.seed.fetch_map_seed", _fake_seed)
    monkeypatch.setattr(
        "kingdoms.discord.maps_forum.sync_maps_forum", _FailingForumSync()
    )

    async def failing_delete(entry_id: str) -> bool:
        raise RuntimeError("delete refused")

    monkeypatch.setattr(service, "delete_map", failing_delete)
    await _run_import(interaction)
    assert "orpheline" in interaction.followup.messages[-1]
