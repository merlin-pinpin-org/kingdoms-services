"""The pool lifecycle journey — the full designer scenario, as a user.

One story, clicked through the real dispatch on SimCord against the
real game service over loopback gRPC (the production _CatalogGateway
composition, plus the GetMapSeed door served by a canned Liquipedia
descriptor so the import runs its real path):

import maps → create a pool → add maps → remove one → re-add it →
rename → publish → re-edit (back to draft) → publish again → archive →
hard-delete by name. Every step asserts the CATALOG state through the
bot's own caller (the RemoteGameDataService), never a fake service.
"""

from __future__ import annotations

from typing import Any

import discord
import grpc
import pytest

from tests.integration.test_catalog_gateway import _FakeDB
from tests.integration.test_civs_sync_journey import _RegistryFake

IMPORT_BUTTON_ID = "admin:pin:modgames:maps:import:guild:aoe2"
CREATE_POOL_BUTTON_ID = "admin:pin:modgames:pools:create:aoe2"

LIQUIPEDIA_SEEDS: dict[str, dict[str, Any]] = {
    "Arabia": {
        "name": "Arabia",
        "description": "The classic open map",
        "resource_url": "https://liquipedia.net/ageofempires2/Arabia",
        "image_url": "https://liquipedia.net/ageofempires2/images/arabia.png",
        "terrain": "Desert",
        "size": "2-8",
        "map_type": "land",
        "licence_ok": True,
        "walls": "No",
        "nomad": "No",
        "tc": "1",
        "player_capacity": "Up to 8",
        "creator": "Ensemble Studios",
        "game_versions": ["DE"],
        "versions": [],
        "tags": ["classic"],
    },
    "Hideout": {
        "name": "Hideout",
        "description": "A defensive pocket",
        "resource_url": "https://liquipedia.net/ageofempires2/Hideout",
        "image_url": "https://liquipedia.net/ageofempires2/images/hideout.png",
        "terrain": "Forest",
        "size": "2-6",
        "map_type": "land",
        "licence_ok": True,
        "walls": "Wood",
        "nomad": "No",
        "tc": "1",
        "player_capacity": "Up to 6",
        "creator": "Ensemble Studios",
        "game_versions": ["DE"],
        "versions": [],
        "tags": [],
    },
}


class _CatalogFake(_FakeDB):
    """The catalog fake, extended to serve the faction and pool lists."""

    async def find_active_factions(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(d)
            for d in self._col("factions").docs.values()
            if d.get("game_key") == game_key and d.get("archived_at") is None
        ]

    async def find_active_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(d)
            for d in self._col("map_pools").docs.values()
            if d.get("game_key") == game_key and d.get("archived_at") is None
        ]

    async def find_all_map_pools(
        self, game_key: str, guild_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            dict(d) for d in self._col("map_pools").docs.values() if d.get("game_key") == game_key
        ]


class _CannedMapSeeds:
    """The game service's Liquipedia door, canned per map name."""

    async def get_map_seed(self, game_key: str, map_key: str) -> dict[str, Any] | None:
        return LIQUIPEDIA_SEEDS.get(map_key)


async def _start_server(catalog: Any, map_seeds: Any) -> tuple[grpc.aio.Server, int]:
    """Serve the real GameData contract over loopback."""
    from kingdoms.game_aoe2.rpc import GameDataServicer, add_gamedata_servicer

    server = grpc.aio.server()
    add_gamedata_servicer(
        server, GameDataServicer(aggregator=None, catalog=catalog, map_seeds=map_seeds)
    )
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


def _modal_fields(shown: Any) -> dict[str, str]:
    """Map each modal control's label to its wire custom_id."""
    fields: dict[str, str] = {}

    def walk(node: Any, label: str = "") -> None:
        if isinstance(node, dict):
            own_label = str(node.get("label") or node.get("text") or label)
            if node.get("type") == 45:  # a Label wrapping one control
                walk(node.get("component"), own_label)
                return
            if "custom_id" in node:
                fields[own_label] = str(node["custom_id"])
            for value in node.values():
                walk(value, own_label)
        elif isinstance(node, list):
            for item in node:
                walk(item, label)

    walk(shown.modal or {})
    return fields


class TestPoolLifecycleJourney:
    """The designer's whole pool story, through the real UI."""

    @pytest.fixture
    def simcord_bot(self, kingdoms_bot):  # type: ignore[no-untyped-def]
        bot = kingdoms_bot

        @bot.tree.command(name="mapspanel")
        async def mapspanel(interaction: discord.Interaction) -> None:
            from kingdoms.discord.admin_panel_games import maps_section_layout

            await interaction.response.send_message(
                view=await maps_section_layout("aoe2", str(interaction.guild_id))
            )

        @bot.tree.command(name="poolspanel")
        async def poolspanel(interaction: discord.Interaction) -> None:
            from kingdoms.discord.admin_panel_games import pools_section_layout

            await interaction.response.send_message(
                view=await pools_section_layout("aoe2", str(interaction.guild_id))
            )

        return bot

    @pytest.mark.simcord(strict_sync=False)
    async def test_the_full_pool_lifecycle(
        self, simcord_env, simcord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        import os

        from kingdoms.core.services.faction_content import FactionContentService
        from kingdoms.core.services.game_data import GameDataService
        from kingdoms.game_aoe2.remote_catalog import RemoteGameDataService
        from kingdoms.game_aoe2.server import _CatalogGateway

        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        alice = guild.add_member(simcord_env.create_user("alice"))

        catalog = _CatalogGateway(
            GameDataService(_CatalogFake()), FactionContentService(_RegistryFake())
        )
        server, port = await _start_server(catalog, _CannedMapSeeds())
        uri = f"127.0.0.1:{port}"
        monkeypatch.setenv("GAME_AOE2_URI", uri)
        import kingdoms.core.models.db as db_module

        monkeypatch.setattr(db_module, "get_async_database", lambda: _RegistryFake())
        remote = RemoteGameDataService(uri)
        monkeypatch.setattr(
            type(simcord_bot.status_service),
            "bot_admins",
            property(lambda self: (str(alice.id),)),
        )

        saved = _reset_caches()
        try:
            # ── import two maps through the real Liquipedia door ──────────
            await alice.slash(channel, "mapspanel")
            panel = channel.last_message
            assert panel is not None
            shown = await alice.click(panel, custom_id=IMPORT_BUTTON_ID)
            assert shown.modal is not None, "the import button opens the bulk modal"
            fields = _modal_fields(shown)
            lines_id = fields["Maps (id, nom ou URL, une par ligne)"]
            done = await alice.submit_modal(shown, {lines_id: "Arabia\nHideout"})
            report = " ".join(m.content for m in done.followups)
            assert "Arabia" in report and "Hideout" in report, report
            maps = {m.name: m for m in await remote.list_maps("aoe2")}
            assert set(maps) == {"Arabia", "Hideout"}, f"both maps imported: {set(maps)}"
            arabia = maps["Arabia"]
            hideout = maps["Hideout"]

            # ── create the pool from the pinned pools panel ───────────────
            await alice.slash(channel, "poolspanel")
            panel = channel.last_message
            assert panel is not None
            shown = await alice.click(panel, custom_id=CREATE_POOL_BUTTON_ID)
            fields = _modal_fields(shown)
            done = await alice.submit_modal(shown, {_modal_fields(shown)["Nom du pool"]: "1v1 Pool"})
            pool = next(p for p in await remote.list_map_pools("aoe2") if p.name == "1v1 Pool")
            assert pool.state == "draft", "a fresh pool is a draft"
            assert pool.map_ids == ()

            # ── the pool's admin menu (the EntityAdminButton path) ───────
            # the menu opens through a posted admin button (the same wire
            # component a pool's forum post carries)
            from kingdoms.discord.entity_admin_menu import EntityAdminButton

            menu_view = discord.ui.LayoutView(timeout=None)
            menu_row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
            menu_row.add_item(EntityAdminButton("pool", pool.id, "aoe2", True))
            menu_view.add_item(menu_row)

            @simcord_bot.tree.command(name="poolbutton")
            async def poolbutton(interaction: discord.Interaction) -> None:
                await interaction.response.send_message(view=menu_view)

            await alice.slash(channel, "poolbutton")
            button_msg = channel.last_message
            assert button_msg is not None
            opened = await alice.click(button_msg, custom_id=f"panel:admin:pool:{pool.id}:aoe2:1")
            menu = opened.response
            assert menu is not None, "the admin menu opens"

            # ── compose: add both maps ────────────────────────────────────
            compose_id = f"games:pool:addmap:{pool.id}"
            shown = await alice.click(menu, custom_id=compose_id)
            fields = _modal_fields(shown)
            assert "Maps à ajouter" in fields, f"the compose modal offers the adds: {fields}"
            await alice.submit_modal(shown, {fields["Maps à ajouter"]: [arabia.id, hideout.id]})
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and set(pool.map_ids) == {arabia.id, hideout.id}

            # ── compose again: remove one map ─────────────────────────────
            shown = await alice.click(menu, custom_id=compose_id)
            fields = _modal_fields(shown)
            assert "Maps à retirer" in fields, f"the members select rides the modal: {fields}"
            await alice.submit_modal(shown, {fields["Maps à retirer"]: [hideout.id]})
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and set(pool.map_ids) == {arabia.id}, "Hideout removed"

            # ── re-add it (the pool's life goes on) ──────────────────────
            shown = await alice.click(menu, custom_id=compose_id)
            fields = _modal_fields(shown)
            await alice.submit_modal(shown, {fields["Maps à ajouter"]: [hideout.id]})
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and set(pool.map_ids) == {arabia.id, hideout.id}

            # ── rename (a draft's action) ────────────────────────────────
            shown = await alice.click(menu, custom_id=f"admin:pin:modgames:pools:rename:{pool.id}")
            fields = _modal_fields(shown)
            await alice.submit_modal(shown, {fields["New name"]: "1v1 Pool v2"})
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and pool.name == "1v1 Pool V2"

            # ── publish ──────────────────────────────────────────────────
            await alice.click(menu, custom_id=f"admin:pin:modgames:pools:publish:{pool.id}")
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and pool.state == "published", "the pool is live"

            # ── re-edit: back to draft ────────────────────────────────────
            await alice.click(menu, custom_id=f"admin:pin:modgames:pools:todraft:{pool.id}")
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and pool.state == "draft", "back to edition"

            # ── publish again, then archive ──────────────────────────────
            await alice.click(menu, custom_id=f"admin:pin:modgames:pools:publish:{pool.id}")
            await alice.click(menu, custom_id=f"admin:pin:modgames:pools:archive:{pool.id}")
            pool = await remote.get_map_pool(pool.id)
            assert pool is not None and pool.state == "closed", "the pool is archived"

            # ── hard delete, by NAME (the confirmation modal) ────────────
            shown = await alice.click(
                menu, custom_id=f"admin:pin:modgames:pools:harddelete:{pool.id}"
            )
            fields = _modal_fields(shown)
            await alice.submit_modal(shown, {fields["Tape l'id exact du pool"]: pool.name})
            assert await remote.get_map_pool(pool.id) is None, "the pool is gone"
            assert all(p.id != pool.id for p in await remote.list_all_map_pools("aoe2"))
        finally:
            await server.stop(0)
            _restore_caches(saved)
            os.environ.pop("GAME_AOE2_URI", None)

    @pytest.mark.simcord(strict_sync=False)
    async def test_a_non_bot_admin_never_mutates(
        self, simcord_env, simcord_bot, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # type: ignore[no-untyped-def]
        import os

        from kingdoms.core.services.faction_content import FactionContentService
        from kingdoms.core.services.game_data import GameDataService
        from kingdoms.game_aoe2.remote_catalog import RemoteGameDataService
        from kingdoms.game_aoe2.server import _CatalogGateway

        guild = simcord_env.create_guild()
        channel = guild.create_text_channel("general")
        mallory = guild.add_member(simcord_env.create_user("mallory"))

        catalog = _CatalogGateway(
            GameDataService(_CatalogFake()), FactionContentService(_RegistryFake())
        )
        server, port = await _start_server(catalog, _CannedMapSeeds())
        monkeypatch.setenv("GAME_AOE2_URI", f"127.0.0.1:{port}")
        import kingdoms.core.models.db as db_module

        monkeypatch.setattr(db_module, "get_async_database", lambda: _RegistryFake())
        remote = RemoteGameDataService(f"127.0.0.1:{port}")

        saved = _reset_caches()
        try:
            await mallory.slash(channel, "poolspanel")
            panel = channel.last_message
            assert panel is not None
            shown = await mallory.click(panel, custom_id=CREATE_POOL_BUTTON_ID)
            assert shown.modal is None, "a non-bot-admin never reaches the creation form"
            answered = shown.response or (shown.followups[-1] if shown.followups else None)
            assert answered is not None, "the guard answers the denial"
            assert "bot admin" in answered.content.lower(), answered.content
            assert await remote.list_map_pools("aoe2") == [], "nothing was created"
        finally:
            await server.stop(0)
            _restore_caches(saved)
            os.environ.pop("GAME_AOE2_URI", None)
