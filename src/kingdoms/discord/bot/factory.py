"""Bot assembly: builds the runnable Kingdoms bot (kingdoms-services#12).

The factory wires, in order: Discord client + command tree, core services
(registry, status), the ``/status`` command, and — as their issues land —
the platform adapter and the core service trio (StateService,
WorkflowEngine, ChannelService). Everything that is not wired yet is
optional, so the bot merges incrementally while ``--preflight`` stays
green in CI (re-scoping note on kingdoms-services#12).

The bot class itself is thin: no manual interaction dispatch (CommandTree
and discord.py Views already route), platform logic stays in the narrow
Protocol seams (ADR-0011: DiscordChannelsPlatform, DiscordRolesPlatform),
and the ``KINGDOMS_BOT_READY`` log contract of the
smoke CI is preserved (kingdoms-services#34).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from kingdoms.core.exceptions import KingdomsError
from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.identity import IdentityService
from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
from kingdoms.core.services.permissions import PermissionService
from kingdoms.core.services.registration import RegistrationService
from kingdoms.core.services.roles import ModRolesService, RolesService
from kingdoms.core.services.state import StateService
from kingdoms.core.services.status import StatusService, parse_bot_admins
from kingdoms.core.services.workflow import WorkflowEngine
from kingdoms.discord.announce import AnnounceConfig, announce_startup
from kingdoms.discord.commands_i18n import CatalogTranslator
from kingdoms.discord.error_handler import answer_kingdoms_error
from kingdoms.discord.error_report import (
    is_benign_interaction_error,
    report_guild_error,
    report_interaction_error,
)

logger = logging.getLogger("kingdoms.bot")

READY_LOG_LINE = "KINGDOMS_BOT_READY"



@dataclass(frozen=True, slots=True)
class BotConfig:
    """Environment-driven bot configuration."""

    mongo_uri: str = ""
    redis_uri: str = ""
    discord_token: str = ""
    bot_admins: str = ""
    deploy_url: str = ""
    deploy_label: str = ""
    deploy_run_url: str = ""
    deploy_infra_label: str = ""
    deploy_infra_url: str = ""
    deploy_kind: str = ""
    deploy_ref: str = ""
    deploy_tree_url: str = ""
    deploy_ts: str = ""
    deploy_run_number: str = ""
    deploy_run_ts: str = ""
    deploy_image: str = ""
    deploy_branch: str = ""
    deploy_pr_title: str = ""
    deploy_commit_ts: str = ""
    deploy_infra_commit_ts: str = ""
    deploy_commit: str = ""
    deploy_ci_run_id: str = ""
    deploy_ci_run_number: str = ""
    deploy_ci_run_ts: str = ""
    sync_guild_id: str = ""
    announce_locale: str = "en"
    announce_enabled: str = "1"
    deploy_env: str = ""
    log_level: str = "INFO"
    config_dir: Path = field(default_factory=lambda: Path("config"))

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> BotConfig:
        """Read the configuration from the environment (or a copy for tests)."""
        env = os.environ if environ is None else environ
        return cls(
            mongo_uri=env.get("MONGO_URI", ""),
            redis_uri=env.get("REDIS_URI", ""),
            discord_token=env.get("DISCORD_TOKEN", ""),
            bot_admins=env.get("BOT_ADMINS", ""),
            deploy_url=env.get("KINGDOMS_DEPLOY_URL", ""),
            deploy_label=env.get("KINGDOMS_DEPLOY_LABEL", ""),
            deploy_run_url=env.get("KINGDOMS_DEPLOY_RUN_URL", ""),
            deploy_infra_label=env.get("KINGDOMS_DEPLOY_INFRA_LABEL", ""),
            deploy_infra_url=env.get("KINGDOMS_DEPLOY_INFRA_URL", ""),
            deploy_kind=env.get("KINGDOMS_DEPLOY_KIND", ""),
            deploy_ref=env.get("KINGDOMS_DEPLOY_REF", ""),
            deploy_tree_url=env.get("KINGDOMS_DEPLOY_TREE_URL", ""),
            deploy_ts=env.get("KINGDOMS_DEPLOY_TS", ""),
            deploy_run_number=env.get("KINGDOMS_DEPLOY_RUN_NUMBER", ""),
            deploy_run_ts=env.get("KINGDOMS_DEPLOY_RUN_TS", ""),
            deploy_image=env.get("KINGDOMS_DEPLOY_IMAGE", ""),
            deploy_branch=env.get("KINGDOMS_DEPLOY_BRANCH", ""),
            deploy_pr_title=env.get("KINGDOMS_DEPLOY_PR_TITLE", ""),
            deploy_commit_ts=env.get("KINGDOMS_DEPLOY_COMMIT_TS", ""),
            deploy_infra_commit_ts=env.get("KINGDOMS_DEPLOY_INFRA_COMMIT_TS", ""),
            deploy_commit=env.get("KINGDOMS_DEPLOY_COMMIT", ""),
            deploy_ci_run_id=env.get("KINGDOMS_DEPLOY_CI_RUN_ID", ""),
            deploy_ci_run_number=env.get("KINGDOMS_DEPLOY_CI_RUN_NUMBER", ""),
            deploy_ci_run_ts=env.get("KINGDOMS_DEPLOY_CI_RUN_TS", ""),
            sync_guild_id=env.get("CICD_GUILD_ID", ""),
            announce_locale=env.get("ANNOUNCE_LOCALE", "en"),
            announce_enabled=env.get("KINGDOMS_ANNOUNCE_ENABLED", "1"),
            deploy_env=env.get("KINGDOMS_DEPLOY_ENV", ""),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )


class KingdomsBot(discord.Client):
    """Discord client with a command tree; core services are attached, not inherited."""

    games: tuple[str, ...] = ()

    def __init__(
        self,
        config: BotConfig,
        status: StatusService,
        logs: LogService | None = None,
        registry: ModRegistry | None = None,
    ) -> None:
        """Create the client, the command tree and attach the services."""
        intents = discord.Intents.default()
        intents.members = True  # the member cache's roster fetch + join/leave events
        super().__init__(intents=intents)
        self.config = config
        self.status_service = status
        self.registry = registry
        self.logs_service = logs
        if logs is not None:
            logs.set_locale_resolver(self._native_guild_locale)
        self.messages = MessageCatalog(config.config_dir)
        self.tree = app_commands.CommandTree(self)
        self._synced = False
        self.admin_channel_service: AdminChannelService | None = None
        self.channel_service: ChannelService | None = None
        self.mod_roles_service: ModRolesService | None = None
        self.permission_service: PermissionService | None = None
        self._provisioned = False
        self._provision_task: asyncio.Task[None] | None = None
        self.roles_service: RolesService | None = None
        self.role_grants_service: Any | None = None
        self.registration_engine: WorkflowEngine | None = None
        self.registration_service: RegistrationService | None = None
        self.identity_service: IdentityService | None = None
        self.home_channel_service: Any | None = None
        self.home_service: Any | None = None
        self.message_registry: Any | None = None
        self.mod_home_builders: dict[str, Any] = {}
        self.mod_profile_enrichers: dict[str, Any] = {}
        self.mod_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self._registration_database: Any | None = None
        self._home_providers: dict[str, Any] = {}
        self.live_client: Any = None
        self.state_service: StateService | None = None

    async def setup_hook(self) -> None:
        """Re-register the persistent UI at every startup (#122).

        Persistent components survive restarts only because their
        state rides the custom_id and their classes are re-registered
        here — a class the factory forgets is dead UI after the next
        deploy (§3b state reconstruction contract).
        """
        from kingdoms.discord.admin_persistent import register_admin_panel_bot, register_admin_persistent_items
        from kingdoms.discord.home import (
            HomeButton,
            ProfileAddAccountButton,
            ProfileRemoveAccountButton,
            ProfileRenameButton,
            ProfileSiteSelect,
        )
        from kingdoms.discord.ui.persistent import register_persistent_items

        register_persistent_items(self)
        register_admin_persistent_items(self)
        register_admin_panel_bot(self)
        self.add_dynamic_items(HomeButton)
        self.add_dynamic_items(ProfileAddAccountButton)
        self.add_dynamic_items(ProfileRemoveAccountButton)
        self.add_dynamic_items(ProfileRenameButton)
        self.add_dynamic_items(ProfileSiteSelect)
        from kingdoms.discord.live import LiveRefreshButton

        self.add_dynamic_items(LiveRefreshButton)
        from kingdoms.discord.admin_panel_games import register_games_admin_items

        register_games_admin_items(self)
        from kingdoms.discord.admin_dm_panel import register_admin_dm_items
        from kingdoms.discord.admin_tree import register_admin_tree_items
        from kingdoms.discord.entity_admin_menu import register_entity_admin_items
        from kingdoms.discord.guild_context import register_guild_context_items
        from kingdoms.discord.maps_pool_flow import register_pool_flow_items

        register_pool_flow_items(self)
        register_entity_admin_items(self)
        register_guild_context_items(self)

        register_admin_dm_items(self)
        register_admin_tree_items(self)
        from kingdoms.discord.views_declared import declare_platform_views
        from kingdoms.discord.views_routing import register_view_router

        declare_platform_views()
        self._wire_declared_views()
        register_view_router(self)

        if self.state_service is not None:
            await self.state_service.start()
            logger.info("STATE SERVICE STARTED (shared Redis state connected)")
        if self.registration_engine is not None:
            await self.registration_engine.start()
            logger.info("REGISTRATION ENGINE STARTED (workflow store connected)")
        if getattr(self, "_home_pin_pending", False):
            self._home_pin_pending = False
    

    async def on_member_join(self, member: discord.Member) -> None:
        """Keep the member cache fresh between TTLs (owner direction)."""
        from kingdoms.discord.member_cache import on_member_join as _join

        _join(member)

    async def on_member_remove(self, member: discord.Member) -> None:
        """Keep the member cache fresh between TTLs (owner direction)."""
        from kingdoms.discord.member_cache import on_member_remove as _remove

        _remove(member)

    async def on_ready(self) -> None:
        """Log the ready marker asserted by smoke CI, then sync commands once."""
        logger.info(
            "%s version=%s user=%s guilds=%d",
            READY_LOG_LINE,
            _bot_version(),
            self.user,
            len(self.guilds),
        )
        announce_enabled = self.config.announce_enabled.strip().lower() not in {"0", "false", "no"}
        await announce_startup(
            self,
            self.status_service,
            AnnounceConfig(
                locale=self.config.announce_locale,
                config_dir=self.config.config_dir,
            ),
            logs_service=self.logs_service,
            deploy_env=self.config.deploy_env,
            enabled=announce_enabled,
            thumbnail_url=self.user.display_avatar.url if self.user else "",
            locale_resolver=self._native_guild_locale if self.logs_service is not None else None,
            commands=self.tree.get_commands(),
            sync_scope=self._sync_scope(),
        )
        self.tree.on_error = self.on_tree_error  # type: ignore[method-assign]
        await self.tree.set_translator(CatalogTranslator(self.messages))
        from kingdoms.discord.pinned_views import set_settings_db_resolver

        set_settings_db_resolver(
            lambda: getattr(self.logs_service, "_db", None) if self.logs_service is not None else None
        )
        from kingdoms.discord.pin_refreshers import register_pin_refreshers

        register_pin_refreshers(self)
        if announce_enabled:
            self._provision_task = asyncio.create_task(self._boot_route_channels())
            from kingdoms.core.services.entity_forum import register_entity_forum_sync
            from kingdoms.discord.factions_forum import factions_forum_spec
            from kingdoms.discord.maps_forum import maps_forum_wiring_ready, register_maps_forum_sync
            from kingdoms.discord.pools_forum import register_pools_forum_sync

            if maps_forum_wiring_ready():
                register_maps_forum_sync()
                register_pools_forum_sync()
                register_entity_forum_sync(factions_forum_spec(self))
        # The live dashboard channel machinery is retired (owner direction
        # 2026-10-23): the live surface is the /live command and the aoe2
        # menu panel — no dedicated autopin channel. The player-events
        # stream (the mods' notifications) stays: it feeds the router.
        from kingdoms.discord.player_status import start_player_status_stream

        start_player_status_stream(self)
        self._live_dashboard_task = _start_live_dashboards(self)
        if self._synced:
            return
        self._synced = True
        try:
            if self.config.sync_guild_id.strip().isdigit():
                guild = discord.Object(id=int(self.config.sync_guild_id))
                await self.tree.sync(guild=guild)
                logger.info("Slash commands synced to guild %s", self.config.sync_guild_id)
            else:
                await self.tree.sync()
                logger.info("Slash commands synced globally")
        except Exception:
            self._synced = False
            logger.exception("SLASH COMMAND SYNC FAILED")

    def _sync_scope(self) -> str:
        """Resolve the command sync scope: the configured guild, or global."""
        guild_id = self.config.sync_guild_id.strip()
        return f"guild {guild_id}" if guild_id.isdigit() else "global"

    async def _boot_route_channels(self) -> None:
        """Route the managed services onto the tree's stored ids (no creation).

        The boot never creates or recreates a channel: a guild member
        who deletes a salon owns that decision. The stored resolutions
        route the services; a deleted salon simply degrades (its id is
        stale) until an admin runs the scope's Synchroniser — the only
        provisioning entry point.
        """
        if self._provisioned:
            return
        self._provisioned = True
        # declare the scopes (views.yaml): the registry drives the sync
        # buttons and the pins — a declaration, never a creation
        try:
            from kingdoms.discord.ui.panels_scopes import register_platform_scopes

            register_platform_scopes()
        except Exception:
            logger.warning("SCOPE REGISTRATION failed — best-effort", exc_info=True)
        if self.config.announce_enabled.strip().lower() in {"0", "false", "no"}:
            logger.info("BOOT ROUTING skipped: announcements disabled (CI/CD smoke bot?)")
            return
        for guild in list(self.guilds):
            guild_id = str(guild.id)
            await self._route_managed_channels(guild_id)
            await self._sync_tree_surfaces(guild_id, guild)
            logger.info("BOOT ROUTING done (guild %s) — nothing created", guild_id)

    async def _route_managed_channels(self, guild_id: str) -> None:
        """Route the managed services onto the tree's stored channel ids.

        The tree is the source of truth: once it provisioned ``bot/logs``
        and ``bot/admin``, the managed services (logs, admin) must point
        at those channels — their name-based adoption/creation fallback
        would otherwise keep resurrecting the pre-tree channels (the
        autoheal loop). Best-effort: a service without a tree channel
        keeps its own resolution (the degraded, tree-less path).
        """
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        try:
            logs_id = await stored_channel_id(guild_id, "bot", "logs")
            if logs_id and self.logs_service is not None:
                await self.logs_service.set_channel(guild_id, logs_id, by="channel-tree")
            admin_id = await stored_channel_id(guild_id, "bot", "admin")
            if admin_id and self.admin_channel_service is not None:
                await self.admin_channel_service.set_channel(guild_id, admin_id, self.status_service.bot_admins)
            home_id = await stored_channel_id(guild_id, "root", "general")
            home_service = getattr(self, "home_channel_service", None)
            if home_id and home_service is not None:
                await home_service.set_channel(guild_id, home_id)
        except Exception:
            logger.warning(
                "MANAGED CHANNEL routing failed (guild %s) — best-effort",
                guild_id,
                exc_info=True,
            )

    async def _sync_tree_surfaces(self, guild_id: str, guild: discord.Guild) -> None:
        """Fill the freshly provisioned tree: forums sync, pins land, now.

        The provisioning owns the channels; this pass owns the content:
        the forum syncs (maps, pools, civs) and the section pins run
        right after the tree exists, on a virgin guild. Best-effort throughout.
        """
        del guild
        try:
            from kingdoms.core.services.entity_forum import sync_entity_forum
            from kingdoms.discord.factions_forum import factions_forum_spec
            from kingdoms.discord.maps_forum import maps_forum_wiring_ready, sync_maps_forum
            from kingdoms.discord.pools_forum import sync_pools_forum

            self._register_scope_pins()
            await self._ensure_all_pins(guild_id)
            if not maps_forum_wiring_ready():
                return
            from kingdoms.core.games.aoe2.content_refresh import refresh_aoe2_content

            try:
                await refresh_aoe2_content()
            except Exception:
                logger.warning("CONTENT REFRESH at boot failed — best-effort", exc_info=True)
            await sync_maps_forum(guild_id, "aoe2", self)
            await sync_pools_forum(guild_id, self)
            await sync_entity_forum(guild_id, self, factions_forum_spec(self))
            logger.info("CHANNEL TREE surfaces synced (guild %s)", guild_id)
        except Exception:
            logger.warning(
                "CHANNEL TREE surface sync failed (guild %s) — best-effort",
                guild_id,
                exc_info=True,
            )

    def _native_guild_locale(self, guild_id: str) -> str:
        """Resolve a guild's native Discord locale (Server Settings language)."""
        from kingdoms.discord.locales import guild_locale_by_id

        return guild_locale_by_id(self, str(guild_id))

    def _translate_panel_label(self, key: str, locale: str) -> str:
        """Render one panels label key in the guild's locale (fallback EN)."""
        if self.messages is None:
            return key
        rendered = self.messages.render(key, locale)
        return rendered if rendered != key else self.messages.render(key, "en")

    async def _audit_channel_drift(self, guild_id: str) -> None:
        """Audit declared policies vs runtime overwrites; log the drift (#57).

        Drift is reported only — repair happens through the on-demand
        sync (kingdoms-services#58). Best-effort: a failed audit never
        blocks startup.
        """
        if self.channel_service is None or self.registry is None:
            return
        try:
            from kingdoms.core.models.db import get_async_database
            from kingdoms.core.services.channel_audit import ChannelAuditService
            from kingdoms.discord.logs_platform import MongoLogsDatabase

            audit = ChannelAuditService(
                database=MongoLogsDatabase(get_async_database()),
                platform=self.channel_service.platform,
                registry=self.registry,
            )
            report = await audit.audit_guild(guild_id)
            if not report.clean:
                logger.warning("CHANNEL AUDIT drift (guild %s): %d findings", guild_id, len(report.findings))
        except Exception:
            logger.warning("CHANNEL AUDIT failed (guild %s) — best-effort", guild_id, exc_info=True)

    async def _resolve_admin_pin_channel(self, guild_id: str) -> str | None:
        """Resolve the tree's ``bot/admin`` (stored id only)."""
        from kingdoms.discord.ui.panels_provisioning import stored_channel_id

        return await stored_channel_id(guild_id, "bot", "admin")

    async def _register_pinned_menus(self: KingdomsBot) -> None:
        """Declare every pinned menu once at boot.

        Every menu (home, admin, scopes) is declared through the views
        registry (config/views.yaml): the scope pins own root/general,
        bot/admin and every scope folder. No legacy pinned menu
        remains — one declaration source, one builder.
        """
    def _games_section_pin_specs(self: KingdomsBot, game_key: str) -> list[Any]:
        """Build the per-forum section pins for one game (aoe2).

        One pinned post per forum — civs, maps, map pools — each
        carrying its section's own buttons (the same views the admin
        panel renders inline). The pins follow the view:<game>-<section>
        registry keys; a deleted forum is recreated by the sync button
        and the pin follows in the same pass.
        """
        from kingdoms.discord.static_pins import StaticPinnedView

        def _resolve_forum(channel_key: str) -> Any:
            async def _resolve(guild_id: str) -> str | None:
                from kingdoms.discord.admin_panel_dynamic import _panel_client_ref
                from kingdoms.discord.ui.panels_provisioning import stored_channel_id

                client = _panel_client_ref()
                guild = client.get_guild(int(guild_id)) if client and guild_id.isdigit() else None
                if guild is None:
                    return None
                stored = await stored_channel_id(guild_id, f"games/{game_key}", channel_key)
                if stored and stored.isdigit():
                    channel = guild.get_channel(int(stored))
                    if channel is not None:
                        return str(channel.id)
                return None

            return _resolve

        async def _build_civs(guild_id: str) -> Any:
            from kingdoms.discord.admin_panel_games import civs_section_layout

            return await civs_section_layout(game_key, guild_id)

        async def _build_maps(guild_id: str) -> Any:
            from kingdoms.discord.admin_panel_games import maps_section_layout

            return await maps_section_layout(game_key, guild_id)

        async def _build_pools(guild_id: str) -> Any:
            from kingdoms.discord.admin_panel_games import pools_section_layout

            return await pools_section_layout(game_key, guild_id)

        return [
            StaticPinnedView(
                key=f"view:{game_key}:civs",
                mark_suffix=f"view:{game_key}:civs",
                resolve_channel=_resolve_forum("civs"),
                build_layout=_build_civs,
                registry=getattr(self, "message_registry", None),
                scope=f"games/{game_key}",
            ),
            StaticPinnedView(
                key=f"view:{game_key}:maps",
                mark_suffix=f"view:{game_key}:maps",
                resolve_channel=_resolve_forum("maps"),
                build_layout=_build_maps,
                registry=getattr(self, "message_registry", None),
                scope=f"games/{game_key}",
            ),
            StaticPinnedView(
                key=f"view:{game_key}:pools",
                mark_suffix=f"view:{game_key}:pools",
                resolve_channel=_resolve_forum("map-pools"),
                build_layout=_build_pools,
                registry=getattr(self, "message_registry", None),
                scope=f"games/{game_key}",
            ),
        ]

    async def _ensure_pinned_menus(self: KingdomsBot, guild_id: str) -> None:
        """Ensure every declared pinned menu exists (the game sections).

        The home/admin/scope menus ensure through the declared pins
        (the registry); this pass covers the per-forum game sections.
        """
        await self._ensure_section_pins(guild_id)

    def _register_scope_pins(self: KingdomsBot) -> None:
        """Declare every scope's home and admin pins (idempotent)."""
        from kingdoms.discord.scope_pins import register_scope_pins

        self._scope_pins = register_scope_pins(self, ("root", "bot", "games", "games/aoe2", "mods"))

    async def _ensure_all_pins(self: KingdomsBot, guild_id: str) -> None:
        """Ensure every registered static pin lives in its channel (now).

        The boot's surface pass: home pins, admin pins, section pins —
        all created or refreshed synchronously, best-effort each.
        """
        from kingdoms.discord.static_pins import ensure_static_pin, registered_static_pins

        for spec in registered_static_pins():
            try:
                await ensure_static_pin(self, spec, guild_id)
            except Exception:
                logger.warning(
                    "PIN ensure failed (%s, guild %s) — best-effort",
                    spec.key,
                    guild_id,
                    exc_info=True,
                )

    async def _ensure_section_pins(self: KingdomsBot, guild_id: str) -> None:
        """Ensure the games section pins exist in their forums (best-effort)."""
        from kingdoms.discord.static_pins import ensure_static_pin, registered_static_pins

        for spec in registered_static_pins():
            if spec.key.startswith("view:aoe2:"):
                try:
                    await ensure_static_pin(self, spec, guild_id)
                except Exception:
                    logger.warning(
                        "GAMES SECTION PIN check failed (%s, guild %s) — best-effort",
                        spec.key,
                        guild_id,
                        exc_info=True,
                    )

    async def _mod_privacy_satisfied(self, definition: Any, guild: discord.Guild | None) -> bool:
        """Place a private mod only where an owner or a bot admin is a member."""
        if guild is None:
            return False
        bot_admins = set(getattr(self.status_service, "bot_admins", ()) or ())
        allowed = bot_admins | set(definition.owner_ids)
        try:
            members = await guild.chunk() if not guild.chunked else guild.members
        except Exception:
            members = guild.members
        return any(str(m.id) in allowed for m in members)

    # Real-time watching of channels/posts/pins is gone: the sync
    # buttons own the healing (on demand, one click). Deleted channels,
    # posts and pinned views come back on the next "Synchroniser" of
    # their scope — no background sweep, nothing in a loop.

    def _wire_declared_views(self) -> None:
        """Wire the declared views and actions (the router's targets).

        Navigation: one handler per declared panel; menus render from
        the registry alone. Actions: the declared behavior ids map to
        the existing DynamicItem flows (the map/pool creation modals),
        guarded exactly like their original buttons.
        """
        from kingdoms.discord import home
        from kingdoms.discord.views_routing import wire_action, wire_view

        wire_view("panel:profile", home._view_profile)
        wire_view("panel:status", home._view_status)
        wire_view("panel:game:aoe2:live", home._view_live)
        wire_view("panel:game:aoe2:players", home._view_users_for_game("aoe2"))
        from kingdoms.discord.providers_panel import view_providers

        wire_view("panel:game:aoe2:providers", view_providers)

        async def _import_map(interaction: discord.Interaction) -> None:
            """Open the Liquipedia import (name/URL/ID, local fallback)."""
            from kingdoms.discord.admin_panel_games import GamesMapImportButton

            item = GamesMapImportButton("aoe2")
            await item.callback(interaction)

        async def _bulk_import_map(interaction: discord.Interaction) -> None:
            """Open the multiline bulk import (one map per line)."""
            from kingdoms.discord.maps_pool_flow import _guard_admin

            if not await _guard_admin(interaction):
                return
            from kingdoms.discord.admin_panel_games import GamesBulkImportModal

            await interaction.response.send_modal(GamesBulkImportModal("aoe2"))

        async def _create_pool(interaction: discord.Interaction) -> None:
            from kingdoms.discord.admin_panel_games import GamesPoolCreateButton

            item = GamesPoolCreateButton("aoe2")
            await item.callback(interaction)

        async def _set_language(interaction: discord.Interaction) -> None:
            """Open the guild language picker (the stored choice wins)."""
            from kingdoms.discord.views_routing import LanguageModal

            current = ""
            if interaction.guild_id is not None and self.logs_service is not None:
                try:
                    current = await self.logs_service.get_locale(str(interaction.guild_id))
                except Exception:
                    current = ""
            await interaction.response.send_modal(
                LanguageModal("Langue / Language", guild_scoped=True, current=current)
            )

        wire_action("menu:admin", "set-language", _set_language)
        wire_action("menu:game:aoe2:admin", "import-map", _import_map)
        wire_action("menu:game:aoe2:admin", "bulk-import-map", _bulk_import_map)
        wire_action("menu:game:aoe2:admin", "create-pool", _create_pool)

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Observe the clicker's Discord client language (best-effort).

        Discord exposes a user's language only inside interactions;
        the interaction-free DMs (boot status, crash reports) render
        in the language the user was last seen using.
        """
        if self.logs_service is None:
            return
        from kingdoms.discord.locales import normalize

        locale = normalize(getattr(interaction, "locale", None))
        user_id = str(getattr(getattr(interaction, "user", None), "id", "") or "")
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        except_logger = logger.debug
        if user_id:
            try:
                await self.logs_service.observe_user_locale(user_id, locale)
            except Exception:
                except_logger("user locale observation failed (user %s)", user_id, exc_info=True)
        if guild_id and user_id:
            try:
                await self.logs_service.observe_guild_member_locale(guild_id, user_id, locale)
            except Exception:
                except_logger("guild member locale observation failed (guild %s)", guild_id, exc_info=True)

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Answer Kingdoms errors with i18n + audit; crash-report the rest."""
        exc = error.__cause__ if error.__cause__ is not None else error
        if is_benign_interaction_error(exc):
            logger.warning(
                "APP COMMAND interaction expired before the answer (user=%s, command=%s) \u2014 answered too slow",
                getattr(interaction.user, "id", None),
                getattr(getattr(interaction, "command", None), "qualified_name", "?"),
            )
            return
        if isinstance(exc, KingdomsError):
            locale = await self._locale_for(interaction)
            answered = await answer_kingdoms_error(
                interaction,
                exc,
                self.messages,
                logs_service=self.logs_service,
                locale=locale,
            )
            if answered:
                return
        logger.exception("APP COMMAND FAILED", exc_info=error)
        await report_interaction_error(
            interaction,
            exc,
            self.config.deploy_tree_url,
            self.logs_service,
            bot=self,
            admin_ids=self.status_service.bot_admins,
        )

    async def _locale_for(self, interaction: discord.Interaction) -> str:
        """Resolve the answering locale: the guild's, or the client's in DM."""
        from kingdoms.discord.locales import interaction_locale

        return interaction_locale(interaction)

    async def crash_report(self, interaction: discord.Interaction, exc: BaseException) -> None:
        """Report a component-callback failure to bot-logs + BOT_ADMINS DMs.

        The panel callbacks catch their own failures to answer the user
        ephemerally; this seam hands the same exception to the #113
        crash reporter (cause, interaction context, GitHub source link).
        """
        await report_interaction_error(
            interaction,
            exc,
            self.config.deploy_tree_url,
            self.logs_service,
            bot=self,
            admin_ids=self.status_service.bot_admins,
        )

    async def on_error(self, event_method: str, /, *args: object, **kwargs: object) -> None:
        """Route unhandled gateway-event failures to each guild's bot-logs."""
        import sys

        exc_info = sys.exc_info()
        logger.exception("UNHANDLED ERROR in %s", event_method, exc_info=exc_info)
        exc = exc_info[1] if exc_info[1] is not None else None
        if exc is None:
            return
        await report_guild_error(
            exc,
            [str(guild.id) for guild in self.guilds],
            self.config.deploy_tree_url,
            self.logs_service,
            bot=self,
            admin_ids=self.status_service.bot_admins,
        )

    async def close(self) -> None:
        """Log the stop lifecycle event, then close the gateway connection."""
        if self.logs_service is not None:
            for guild in self.guilds:
                from kingdoms.discord.locales import guild_locale

                locale = guild_locale(guild)
                event = LifecycleEvent(
                    kind="stop",
                    message=self.messages.render("lifecycle.stop", locale) if self.messages else "Bot shutting down.",
                )
                await self.logs_service.log_event(str(guild.id), event)
        if self._live_dashboard_task is not None:
            self._live_dashboard_task.cancel()
        if self.registration_engine is not None:
            await self.registration_engine.stop()
        if self.state_service is not None:
            await self.state_service.close()
        await super().close()


def create_bot(config: BotConfig | None = None) -> KingdomsBot:
    """Build the Kingdoms bot: client, tree, core services and commands."""
    resolved = config or BotConfig.from_env()
    registry = ModRegistry(load_mod_definitions(Path(resolved.config_dir)))
    games_dir = Path(resolved.config_dir) / "games"
    games = tuple(sorted(entry.name for entry in games_dir.iterdir() if entry.is_dir())) if games_dir.is_dir() else ()
    status = StatusService(
        registry=registry,
        games=games,
        bot_admins=parse_bot_admins(resolved.bot_admins),
        deploy_url=resolved.deploy_url,
        deploy_label=resolved.deploy_label,
        deploy_run_url=resolved.deploy_run_url,
        deploy_infra_label=resolved.deploy_infra_label,
        deploy_infra_url=resolved.deploy_infra_url,
        deploy_kind=resolved.deploy_kind,
        deploy_ref=resolved.deploy_ref,
        deploy_tree_url=resolved.deploy_tree_url,
        deploy_ts=resolved.deploy_ts,
        deploy_run_number=resolved.deploy_run_number,
        deploy_run_ts=resolved.deploy_run_ts,
        deploy_image=resolved.deploy_image,
        deploy_branch=resolved.deploy_branch,
        deploy_pr_title=resolved.deploy_pr_title,
        deploy_commit_ts=resolved.deploy_commit_ts,
        deploy_infra_commit_ts=resolved.deploy_infra_commit_ts,
        deploy_commit=resolved.deploy_commit,
        deploy_ci_run_id=resolved.deploy_ci_run_id,
        deploy_ci_run_number=resolved.deploy_ci_run_number,
        deploy_ci_run_ts=resolved.deploy_ci_run_ts,
    )
    shared_state = _build_shared_state(resolved)
    bot = KingdomsBot(config=resolved, status=status, registry=registry)
    bot.games = games
    bot.state_service = shared_state
    bot.logs_service = _build_log_service(resolved, bot, state=shared_state)
    # the freshly built service carries the native-locale resolver: the
    # constructor's wiring died with the placeholder service (the 2026-10-20
    # English-autopin bug — the resolver must bind the real service).
    if bot.logs_service is not None:
        bot.logs_service.set_locale_resolver(bot._native_guild_locale)
    roles_service = _build_roles_service(resolved, bot, state=shared_state)
    bot.roles_service = roles_service
    bot.role_grants_service = _build_role_grants_service(resolved)
    admin_channel_service = _build_admin_channel_service(resolved, bot, roles_service, state=shared_state)
    bot.admin_channel_service = admin_channel_service
    channel_service, mod_roles_service = _build_mod_provisioning(resolved, bot, registry, state=shared_state)
    bot.channel_service = channel_service
    bot.mod_roles_service = mod_roles_service
    bot.permission_service = _build_permission_service(resolved, bot, mod_roles_service, status.bot_admins)

    from kingdoms.discord.admin import register_admin_command
    from kingdoms.discord.home import register_live_command
    from kingdoms.discord.registration import register_registration_command
    from kingdoms.discord.status import register_status_command

    guild_id = resolved.sync_guild_id.strip()
    sync_target = f"guild {guild_id}" if guild_id.isdigit() else "global"
    register_status_command(bot.tree, status, sync_target=sync_target, catalog=bot.messages)
    register_live_command(bot.tree, catalog=bot.messages, games=games)
    registration_engine, registration_service = _build_registration(resolved, state=shared_state)
    bot.registration_engine = registration_engine
    bot.registration_service = registration_service
    bot.identity_service = _build_identity_service(resolved, state=shared_state)
    home_channel_service = _build_home_channel_service(resolved, bot)
    bot.home_channel_service = home_channel_service
    from kingdoms.discord.messages_platform import build_message_registry

    bot.message_registry = build_message_registry()
    from kingdoms.core.services.home import HomeService
    from kingdoms.discord.home import register_home_command

    class _DiscordModHomeViews:
        """Bridge the bot's mod home builders onto the HomeService seam."""

        def __init__(self, builders: dict[str, Any]) -> None:
            self._builders = builders

        def mod_home_view(self, mod: str) -> Any | None:
            """Return the home-view builder registered by the given mod."""
            return self._builders.get(mod)

    bot.home_service = HomeService(registry, _DiscordModHomeViews(bot.mod_home_builders))
    bot._registration_database = _build_registration_database(resolved)
    bot._home_providers = {"aoe2": _build_home_provider(resolved)} if _build_home_provider(resolved) else {}
    import os as _os

    _core_uri = _os.environ.get("CORE_URI", "")
    _game_uri = _os.environ.get("GAME_AOE2_URI", "")
    if _game_uri:
        from kingdoms.game_aoe2.client import GameDataClient

        bot.live_client = GameDataClient(_game_uri)
    elif _core_uri:
        from kingdoms.core.rpc.live import LiveClient

        bot.live_client = LiveClient(_core_uri)
    else:
        bot.live_client = None
    register_home_command(bot.tree, bot.home_service, catalog=bot.messages)
    if home_channel_service is not None:
        bot._home_pin_pending = True
    register_registration_command(
        bot.tree,
        registration_engine,
        registration_service,
        catalog=bot.messages,
        bot=bot,
    )
    register_admin_command(
        bot.tree,
        bot_admins=status.bot_admins,
        logs_service=bot.logs_service,
        roles_service=roles_service,
        catalog=bot.messages,
        admin_channel_service=admin_channel_service,
        error_reporter=bot.crash_report,
    )
    from kingdoms.discord.wiring import set_guild_access_platform

    set_guild_access_platform(
        games,
        tuple(registry.enabled()),
        tuple((name, definition.required_games) for name, definition in registry.enabled().items()),
    )
    return bot


def _start_live_dashboards(bot: KingdomsBot) -> asyncio.Task[None] | None:
    """Run the per-game live dashboard refresh loops when wired.

    Generic (owner direction 2026-10-23): every installed game with a
    configured game service gets its own dashboard stream and channel.
    The game-service URI convention is ``GAME_<KEY>_URI`` (uppercase
    game key); aoe2 is the first game.
    """
    from kingdoms.discord.live import start_live_dashboard_refresh
    from kingdoms.discord.messages_platform import build_message_registry

    registry = build_message_registry()
    if registry is None:
        return None
    clients: dict[str, Any] = {}
    for game_key in getattr(bot, "games", ()) or ():
        uri = os.environ.get(f"GAME_{game_key.upper()}_URI", "")
        if not uri:
            continue
        if game_key == "aoe2":
            from kingdoms.game_aoe2.client import GameDataClient

            clients[game_key] = GameDataClient(uri)
        else:
            logger.warning("live dashboard: no client for game %s yet", game_key)
    if not clients:
        return None
    task: Any = asyncio.create_task(start_live_dashboard_refresh(bot, clients, registry))
    bot._live_refresh_task = task  # type: ignore[attr-defined]  # the refresh button revives it
    return task  # type: ignore[no-any-return]


def _build_home_channel_service(
    config: BotConfig,
    bot: KingdomsBot,
) -> Any | None:
    """Wire the 🏛-home managed channel (cache-aside like the admin channel).

    Returns None when the stores are not configured: the home degrades
    to the /home command only.
    """
    if not config.mongo_uri or not config.redis_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.managed_channel import ManagedChannelService
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.home_platform import DiscordHomeChannelPlatform
        from kingdoms.discord.logs_platform import MongoLogsDatabase

        return ManagedChannelService(
            platform=DiscordHomeChannelPlatform(bot),
            database=MongoLogsDatabase(get_async_database()),
            category="bot_home",
            name="🏛-home",
            state=StateService(redis_uri=config.redis_uri),
        )
    except Exception:
        logger.exception("HOME CHANNEL SERVICE WIRING FAILED — /home stays command-only")
        return None


def _build_registration_database(config: BotConfig) -> Any | None:
    """Return the registration database seam (roster view), None unwired."""
    if not config.mongo_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.discord.registration_platform import MongoRegistrationDatabase

        return MongoRegistrationDatabase(get_async_database())
    except Exception:
        return None


def _build_home_provider(config: BotConfig) -> Any | None:
    """Return the AoE2 live adapter for the games view, None unwired."""
    if not config.redis_uri:
        return None
    try:
        from kingdoms.ext_librematch.adapter import LibrematchAdapter

        return LibrematchAdapter(api_key=os.environ.get("AOE2_API_KEY", ""))
    except Exception:
        return None


def _build_permission_service(
    config: BotConfig,
    bot: KingdomsBot,
    mod_roles_service: ModRolesService | None,
    bot_admins: tuple[str, ...],
) -> PermissionService:
    """Wire the runtime permission checks (click-time authorization).

    Always available: the member seam reads live Discord roles, and
    the mod-role resolver falls back to the declared display names
    even when the mapping store is not configured. Without Redis the
    role resolution skips the cache — the checks stay correct, merely
    uncached.
    """
    from kingdoms.discord.roles_platform import DiscordRolesPlatform

    class _DeclaredRolesResolver:
        """Resolve role keys through the mappings, else the platform lookup."""

        def __init__(self, mod_roles: ModRolesService | None, platform: DiscordRolesPlatform) -> None:
            self._mod_roles = mod_roles
            self._platform = platform

        async def resolve_role_id(self, guild_id: str, mod: str, role_key: str) -> str | None:
            """Resolve a role key; an undeclared mod reads as unmapped."""
            if self._mod_roles is not None:
                try:
                    return await self._mod_roles.resolve_role_id(guild_id, mod, role_key)
                except KeyError:
                    return None
            return None

    resolver = _DeclaredRolesResolver(mod_roles_service, DiscordRolesPlatform(bot))
    return PermissionService(
        members=DiscordRolesPlatform(bot),
        roles=resolver,
        bot_admins=bot_admins,
    )


def _build_registration(
    config: BotConfig,
    state: StateService | None = None,
) -> tuple[WorkflowEngine | None, RegistrationService | None]:
    """Wire the DM enrollment stack (#133): engine + AoE2-validated service.

    Returns (None, None) when Mongo/Redis are not configured (unit tests,
    local runs) — the /register command then answers with a note.
    """
    if not config.mongo_uri or not config.redis_uri:
        return None, None
    import os

    from kingdoms.core.models.db import get_async_database
    from kingdoms.core.services.state import StateService
    from kingdoms.core.services.workflow import MongoWorkflowStore, WorkflowEngine
    from kingdoms.core.workflows.registration import RegistrationWorkflow
    from kingdoms.discord.registration_platform import (
        Aoe2ProfileValidationSeam,
        MongoRegistrationDatabase,
    )

    database = get_async_database()
    engine = WorkflowEngine(
        MongoWorkflowStore(database),
        state or StateService(redis_uri=config.redis_uri or os.environ.get("REDIS_URI")),
    )
    service = RegistrationService(
        MongoRegistrationDatabase(database),
        profile_seams={"aoe2": Aoe2ProfileValidationSeam()},
    )
    engine.register_workflow(RegistrationWorkflow(service))
    return engine, service


def _build_identity_service(config: BotConfig, state: StateService | None = None) -> IdentityService | None:
    """Wire the identity service (profile renames) onto Mongo/Redis.

    Returns None when Mongo/Redis are not configured (unit tests, local
    runs) — the profile view then keeps the default header and the
    rename button answers with the not-configured note.
    """
    if not config.mongo_uri or not config.redis_uri:
        return None
    from kingdoms.core.models.db import get_async_database
    from kingdoms.discord.identity_platform import MongoIdentityDatabase

    cache = state
    if cache is None:
        from kingdoms.core.services.state import StateService

        cache = StateService(redis_uri=config.redis_uri)
    return IdentityService(MongoIdentityDatabase(get_async_database()), cache)


def _build_shared_state(config: BotConfig) -> StateService | None:
    """One shared StateService for the whole bot (single Redis connection pool).

    Started in ``setup_hook`` and closed in ``close``; the services hold it
    as their cache-aside store. Returns None without Redis (unit tests,
    local runs) — the consumers already degrade to uncached paths.
    """
    if not config.redis_uri:
        return None
    return StateService(redis_uri=config.redis_uri)


def _build_role_grants_service(config: BotConfig) -> Any | None:
    """Wire the per-guild role-grant mappings; None without Mongo."""
    if not config.mongo_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.role_grants import (
            MongoRoleGrantsDatabase,
            RoleGrantsService,
        )

        return RoleGrantsService(MongoRoleGrantsDatabase(get_async_database()))
    except Exception:
        logger.exception("ROLE GRANTS SERVICE WIRING FAILED — role mapping disabled")
        return None


def _build_roles_service(config: BotConfig, bot: KingdomsBot, state: StateService | None = None) -> RolesService | None:
    """Wire the Discord platform seam + the shared Redis state into RolesService.

    Returns None when Redis is not configured (unit tests, local runs):
    the runtime guards degrade to BOT_ADMINS + guild administrators.
    """
    if not config.redis_uri:
        return None
    try:
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.roles_platform import DiscordRolesPlatform

        return RolesService(platform=DiscordRolesPlatform(bot), cache=state or StateService(redis_uri=config.redis_uri))
    except Exception:
        logger.exception("ROLES SERVICE WIRING FAILED — runtime role checks degrade")
        return None


def _build_mod_provisioning(
    config: BotConfig,
    bot: KingdomsBot,
    registry: ModRegistry,
    state: StateService | None = None,
) -> tuple[ChannelService | None, ModRolesService | None]:
    """Wire Mongo + the Discord seams + Redis into the mod provisioning pair.

    Returns (None, None) when the stores are not configured (unit tests,
    local runs): mod channels and roles are provisioned lazily on first
    use instead of at startup.
    """
    if not config.mongo_uri or not config.redis_uri:
        return None, None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.channels_platform import DiscordChannelsPlatform
        from kingdoms.discord.logs_platform import MongoLogsDatabase
        from kingdoms.discord.roles_platform import DiscordRolesPlatform, MongoRolesDatabase

        database = get_async_database()
        state = state or StateService(redis_uri=config.redis_uri)
        channel_service = ChannelService(
            database=MongoLogsDatabase(database),
            platform=DiscordChannelsPlatform(bot),
            cache=state,
            registry=registry,
        )
        mod_roles_service = ModRolesService(
            database=MongoRolesDatabase(database),
            platform=DiscordRolesPlatform(bot),
            registry=registry,
            members=DiscordRolesPlatform(bot),
            cache=state,
        )
        return channel_service, mod_roles_service
    except Exception:
        logger.exception("MOD PROVISIONING WIRING FAILED — mod channels/roles provision lazily")
        return None, None


def _build_admin_channel_service(
    config: BotConfig,
    bot: KingdomsBot,
    roles_service: RolesService | None,
    state: StateService | None = None,
) -> AdminChannelService | None:
    """Wire Mongo + the Discord platform seam + Redis into AdminChannelService.

    Returns None when the stores are not configured: the admin messages
    degrade to the invoking context (ephemeral answers).
    """
    if roles_service is None or not config.mongo_uri or not config.redis_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.logs_platform import MongoLogsDatabase
        from kingdoms.discord.roles_platform import DiscordAdminChannelPlatform

        return AdminChannelService(
            platform=DiscordAdminChannelPlatform(bot),
            database=MongoLogsDatabase(get_async_database()),
            roles_service=roles_service,
            state=state or StateService(redis_uri=config.redis_uri),
        )
    except Exception:
        logger.exception("ADMIN CHANNEL SERVICE WIRING FAILED — admin messages degrade")
        return None


def _build_log_service(config: BotConfig, bot: KingdomsBot, state: StateService | None = None) -> LogService | None:
    """Wire Mongo (async) + the Discord platform seam + Redis into LogService.

    Returns None when the stores are not configured (unit tests, local
    runs): the announcement and lifecycle logging degrade to a skip.
    """
    if not config.mongo_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.logs_platform import DiscordLogsPlatform, MongoLogsDatabase

        state = state or StateService(redis_uri=config.redis_uri or None)
        return LogService(
            database=MongoLogsDatabase(get_async_database()),
            platform=DiscordLogsPlatform(bot),
            state=state,
            catalog=MessageCatalog(config.config_dir),
        )
    except Exception:
        logger.exception("LOG SERVICE WIRING FAILED — lifecycle logging disabled")
        return None


def run_bot(config: BotConfig | None = None) -> None:
    """Build the bot and block until the gateway connection ends."""
    resolved = config or BotConfig.from_env()
    bot = create_bot(resolved)
    bot.run(resolved.discord_token, log_handler=None)


def _bot_version() -> str:
    """Return the installed kingdoms version."""
    from kingdoms import __version__

    return __version__
