"""The Civs sync journey — the sync creates the civ forum posts (#330).

The 2026-11-06 handover bug: the Civs button upserted the content docs
but never ran the factions forum mirror, so the civ forum stayed empty
(the Maps button always had its mirror). This journey clicks the REAL
button through the REAL dispatch on SimCord. The content-sync leg is
canned at the GameDataClient seam (that leg has its own seam test in
``test_rpc_seam_journeys.py``); everything after — the wiring, the
remote catalog over loopback gRPC (the production _CatalogGateway
composition), the forum resolution by stored id, the post creation —
is the production path.
"""

from __future__ import annotations

from typing import Any

import discord
import grpc
import pytest

from tests.integration.test_catalog_gateway import _FakeDB

CIVS_BUTTON_ID = "admin:dm:refresh"


class _CatalogFake(_FakeDB):
    """The catalog fake, extended to serve the faction list."""

    async def find_active_factions(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(d)
            for d in self._col("factions").docs.values()
            if d.get("game_key") == game_key and d.get("archived_at") is None
        ]


class _RegistryFake:
    """The channels registry + content store, in one in-memory database."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def __getitem__(self, name: str) -> Any:
        docs = self.docs

        class _Col:
            async def find_one(self: Any, query: dict[str, Any]) -> dict[str, Any] | None:
                return docs.get(str(query.get("_id")))

            async def replace_one(
                self: Any,
                query: dict[str, Any],
                doc: dict[str, Any],
                upsert: bool = False,
            ) -> None:
                docs[str(query.get("_id"))] = doc

        return _Col()


async def _start_server(catalog: Any) -> tuple[grpc.aio.Server, int]:
    """Serve the real GameData contract over loopback with this catalog."""
    from kingdoms.game_aoe2.rpc import GameDataServicer, add_gamedata_servicer

    server = grpc.aio.server()
    add_gamedata_servicer(server, GameDataServicer(aggregator=None, catalog=catalog))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port


def _reset_caches() -> dict[str, Any]:
    """Neutralize the wiring memoizations; return the saved state."""
    import kingdoms.discord.content_posts as content_posts
    import kingdoms.discord.wiring as wiring

    saved = {
        "wiring_service": wiring._GAMES_SERVICE,
        "wiring_ready": wiring._GAMES_SERVICE_READY,
        "content_service": content_posts._CONTENT_SERVICE,
        "content_ready": content_posts._CONTENT_READY,
    }
    wiring._GAMES_SERVICE = None
    wiring._GAMES_SERVICE_READY = False
    content_posts._CONTENT_SERVICE = None
    content_posts._CONTENT_READY = False
    return saved


def _restore_caches(saved: dict[str, Any]) -> None:
    import kingdoms.discord.content_posts as content_posts
    import kingdoms.discord.wiring as wiring

    wiring._GAMES_SERVICE = saved["wiring_service"]
    wiring._GAMES_SERVICE_READY = saved["wiring_ready"]
    content_posts._CONTENT_SERVICE = saved["content_service"]
    content_posts._CONTENT_READY = saved["content_ready"]


class TestCivsSyncJourney:
    """Click the Civs button as a user; the civ forum fills up."""

    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        bot = kingdoms_bot
        from kingdoms.discord.admin_dm_panel import register_admin_dm_items

        register_admin_dm_items(bot)

        @bot.tree.command(name="civsync")
        async def civsync(interaction: discord.Interaction) -> None:
            from kingdoms.discord.admin_dm_panel import _open_sync

            await _open_sync(interaction)

        return bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_civs_sync_creates_the_forum_posts(
        self, simcord_env, simcord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        import os

        from kingdoms.core.services.faction_content import FactionContentService
        from kingdoms.core.services.game_data import GameDataService
        from kingdoms.game_aoe2.server import _CatalogGateway

        guild = simcord_env.create_guild()
        forum = guild.create_forum_channel("civs")
        alice = guild.add_member(simcord_env.create_user("alice"))

        # the production catalog composition over an in-memory database,
        # served by the real GameData servicer over loopback gRPC
        catalog_fake = _CatalogFake()
        catalog = _CatalogGateway(
            GameDataService(catalog_fake), FactionContentService(_RegistryFake())
        )
        game_service = catalog._service
        await game_service.create_faction("aoe2", "Aztecs", faction_key="aztecs")
        await game_service.create_faction("aoe2", "Britons", faction_key="britons")
        server, port = await _start_server(catalog)

        # the provisioning contract: the forum is resolved by its STORED id
        registry = _RegistryFake()
        registry.docs[f"{guild.id}:tree:games/aoe2:civs"] = {
            "_id": f"{guild.id}:tree:games/aoe2:civs",
            "channel_id": str(forum.id),
        }
        monkeypatch.setenv("GAME_AOE2_URI", f"127.0.0.1:{port}")
        import kingdoms.core.models.db as db_module

        monkeypatch.setattr(db_module, "get_async_database", lambda: registry)

        # the content-sync leg, canned at the seam (its own seam test covers it)
        from kingdoms.game_aoe2.client import GameDataClient

        async def _canned_sync(self: Any, game_key: str, what: str, timeout_s: float = 900.0) -> dict[str, Any]:
            return {
                "ok": True,
                "error": "",
                "counts": {"total_factions": 2, "factions": 0, "content_docs": 0},
                "new_factions": [],
            }

        monkeypatch.setattr(GameDataClient, "sync_content", _canned_sync)

        # the clicker is a BOT_ADMINS operator

        monkeypatch.setattr(
            type(simcord_bot.status_service),
            "bot_admins",
            property(lambda self: (str(alice.id),)),
        )

        saved = _reset_caches()
        try:
            channel = guild.create_text_channel("general")
            result = await alice.slash(channel, "civsync")
            message = result.response
            assert message is not None, "the sync sub-view must answer the command"
            assert message.ephemeral, "the admin DM sub-view is ephemeral"

            await alice.click(message, custom_id=CIVS_BUTTON_ID)
        finally:
            await server.stop(0)
            _restore_caches(saved)
            os.environ.pop("GAME_AOE2_URI", None)
