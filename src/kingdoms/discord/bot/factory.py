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

PINNED_MENU_CHECK_INTERVAL = 3600


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

    def __init__(
        self,
        config: BotConfig,
        status: StatusService,
        logs: LogService | None = None,
        registry: ModRegistry | None = None,
    ) -> None:
        """Create the client, the command tree and attach the services."""
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.status_service = status
        self.registry = registry
        self.logs_service = logs
        self.messages = MessageCatalog(config.config_dir)
        self.tree = app_commands.CommandTree(self)
        self._synced = False
        self.admin_channel_service: AdminChannelService | None = None
        self.channel_service: ChannelService | None = None
        self.mod_roles_service: ModRolesService | None = None
        self.permission_service: PermissionService | None = None
        self._provisioned = False
        self._provision_task: asyncio.Task[None] | None = None
        self._pin_task: asyncio.Task[None] | None = None
        self._live_dashboard_task: asyncio.Future[None] | None = None
        self._maps_forum_task: asyncio.Task[None] | None = None
        self._pools_forum_task: asyncio.Task[None] | None = None
        self._factions_forum_task: asyncio.Task[None] | None = None
        self.roles_service: RolesService | None = None
        self.registration_engine: WorkflowEngine | None = None
        self.registration_service: RegistrationService | None = None
        self.identity_service: IdentityService | None = None
        self.home_channel_service: Any | None = None
        self.staff_service: Any | None = None
        self.home_service: Any | None = None
        self.message_registry: Any | None = None
        self.mod_home_builders: dict[str, Any] = {}
        self.mod_profile_enrichers: dict[str, Any] = {}
        self.mod_tasks: dict[str, list[asyncio.Task[None]]] = {}
        self.season_roles_service: Any | None = None
        self.season_service: Any | None = None
        self._registration_database: Any | None = None
        self._home_providers: dict[str, Any] = {}
        self._home_pin_task: asyncio.Task[None] | None = None
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
        )
        from kingdoms.discord.ui.persistent import register_persistent_items

        register_persistent_items(self)
        register_admin_persistent_items(self)
        register_admin_panel_bot(self)
        self.add_dynamic_items(HomeButton)
        self.add_dynamic_items(ProfileAddAccountButton)
        self.add_dynamic_items(ProfileRemoveAccountButton)
        self.add_dynamic_items(ProfileRenameButton)
        from kingdoms.discord.admin_panel_games import register_games_admin_items, register_games_admin_section

        register_games_admin_section()
        register_games_admin_items(self)
        from kingdoms.discord.admin_dm_panel import register_admin_dm_items
        from kingdoms.discord.guild_access_request import register_guild_access_request_items
        from kingdoms.discord.guild_context import register_guild_context_items
        from kingdoms.discord.maps_pool_flow import register_pool_flow_items

        register_pool_flow_items(self)
        register_guild_context_items(self)
        register_admin_dm_items(self)
        register_guild_access_request_items(self)

        if self.state_service is not None:
            await self.state_service.start()
            logger.info("STATE SERVICE STARTED (shared Redis state connected)")
        if self.registration_engine is not None:
            await self.registration_engine.start()
            logger.info("REGISTRATION ENGINE STARTED (workflow store connected)")
        if getattr(self, "_home_pin_pending", False):
            self._home_pin_pending = False
            self._home_pin_task = asyncio.create_task(_maintain_pinned_home_menu(self))

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
            locale_resolver=self.logs_service.get_locale if self.logs_service is not None else None,
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
            self._provision_task = asyncio.create_task(self._provision_default_channels())
            self._pin_task = asyncio.create_task(self._maintain_pinned_menus())
            from kingdoms.core.services.entity_forum import start_entity_forum_sync
            from kingdoms.discord.factions_forum import factions_forum_spec
            from kingdoms.discord.maps_forum import maps_forum_wiring_ready, start_maps_forum_sync
            from kingdoms.discord.pools_forum import start_pools_forum_sync

            if maps_forum_wiring_ready():
                self._maps_forum_task = start_maps_forum_sync(self)
                self._pools_forum_task = start_pools_forum_sync(self)
                self._factions_forum_task = start_entity_forum_sync(self, factions_forum_spec(self), startup_delay_s=20)
            from kingdoms.core.services.mod_entrypoint import run_mod_hook

            if self.registry is not None:
                for mod_name in self.registry.enabled():
                    run_mod_hook(self, mod_name, "setup_hook")
        self._live_dashboard_task = _start_live_dashboard(self)
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

    async def _provision_default_channels(self) -> None:
        """Create the default channels (🛰-bot-logs, 🛡-bot-admins) where missing.

        Both resolutions are idempotent (cache-aside: Redis → MongoDB →
        adoption → creation), so a guild already provisioned costs one
        cache read. The transparency contract rides along on the admin
        channel (role provisioned, BOT_ADMINS synced in). Silenced by
        KINGDOMS_ANNOUNCE_ENABLED=0: the CI/CD smoke bot boots against
        the shared guilds and must never touch their channels.
        """
        if self._provisioned:
            return
        if self.config.announce_enabled.strip().lower() in {"0", "false", "no"}:
            logger.info("DEFAULT CHANNEL provisioning skipped: announcements disabled (CI/CD smoke bot?)")
            self._provisioned = True
            return
        self._provisioned = True
        admin_ids = self.status_service.bot_admins
        for guild in list(self.guilds):
            guild_id = str(guild.id)
            if self.logs_service is not None:
                try:
                    await self.logs_service.resolve_channel(guild_id)
                except Exception:
                    logger.warning("LOGS CHANNEL provisioning failed (guild %s) — best-effort", guild_id, exc_info=True)
            if self.admin_channel_service is not None:
                try:
                    await self.admin_channel_service.resolve_channel(guild_id, admin_ids)
                except Exception:
                    logger.warning(
                        "ADMIN CHANNEL provisioning failed (guild %s) — best-effort", guild_id, exc_info=True
                    )
            if self.channel_service is not None and self.mod_roles_service is not None and self.registry is not None:
                await self._provision_mods(
                    guild_id,
                    channel_service=self.channel_service,
                    mod_roles_service=self.mod_roles_service,
                    registry=self.registry,
                )
            logger.info("DEFAULT CHANNELS provisioned (guild %s)", guild_id)
            await self._audit_channel_drift(guild_id)

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

    async def _maintain_pinned_menus(self) -> None:
        """Keep the pinned admin menu alive in every guild (self-healing).

        The admin channel hosts a permanent pinned menu: the same
        panel as /admin, guarded at click time. The periodic check
        re-creates it when it disappears (unpinned, deleted, channel
        re-provisioned) — the surface never depends on an admin
        remembering to type the command.
        """
        from kingdoms.discord.admin_panel_pin import ensure_pinned_admin_menu

        await asyncio.sleep(5)
        while True:
            for guild in list(self.guilds):
                if self.logs_service is None:
                    break
                try:
                    await ensure_pinned_admin_menu(
                        self,
                        str(guild.id),
                        self.logs_service,
                        self.roles_service,
                        self.admin_channel_service,
                        self.status_service.bot_admins,
                        self.messages,
                    )
                except Exception:
                    logger.warning("PINNED ADMIN MENU check failed (guild %s) — best-effort", guild.id, exc_info=True)
            await asyncio.sleep(PINNED_MENU_CHECK_INTERVAL)

    async def _provision_mods(
        self,
        guild_id: str,
        channel_service: ChannelService,
        mod_roles_service: ModRolesService,
        registry: ModRegistry,
    ) -> None:
        """Provision channels and roles for the guild's granted mods (best-effort, idempotent).

        Nothing is active by default: only the mods the guild was
        granted through the access service (bot-admin DM approval) are
        provisioned — the per-guild activation seam.
        """
        from kingdoms.discord.wiring import build_guild_access_service

        access = build_guild_access_service()
        if access is not None:
            try:
                granted = await access.enabled_mods(guild_id)
            except Exception:
                logger.warning("GUILD ACCESS read failed (guild %s) — best-effort", guild_id, exc_info=True)
                return
            if not granted:
                logger.info("MODS SKIPPED (guild %s) — no access granted yet", guild_id)
                return
        for mod_name, definition in registry.enabled().items():
            if access is not None and mod_name not in granted:
                continue
            try:
                await channel_service.setup_mod_channels(guild_id, mod_name)
                await mod_roles_service.setup_mod_roles(guild_id, mod_name)
                logger.info(
                    "MOD %s provisioned (guild %s, %d channels, %d roles)",
                    mod_name,
                    guild_id,
                    len(definition.channel_categories),
                    len(definition.roles),
                )
            except Exception:
                logger.warning("MOD %s provisioning failed (guild %s) — best-effort", mod_name, guild_id, exc_info=True)

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
        """Resolve the answering locale: the guild's, or the user's in DM."""
        if self.logs_service is None:
            return "en"
        if interaction.guild_id is not None:
            return await self.logs_service.get_locale(str(interaction.guild_id))
        return await self.logs_service.get_user_locale(str(interaction.user.id))

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
                locale = await self.logs_service.get_locale(str(guild.id))
                event = LifecycleEvent(
                    kind="stop",
                    message=self.messages.render("lifecycle.stop", locale) if self.messages else "Bot shutting down.",
                )
                await self.logs_service.log_event(str(guild.id), event)
        from kingdoms.core.services.mod_entrypoint import run_mod_hook

        if self.registry is not None:
            for mod_name in self.registry.enabled():
                run_mod_hook(self, mod_name, "close")
        for task in (
            self._live_dashboard_task,
            self._maps_forum_task,
            self._pools_forum_task,
            self._factions_forum_task,
        ):
            if task is not None:
                task.cancel()
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
    bot.state_service = shared_state
    bot.logs_service = _build_log_service(resolved, bot, state=shared_state)
    roles_service = _build_roles_service(resolved, bot, state=shared_state)
    bot.roles_service = roles_service
    admin_channel_service = _build_admin_channel_service(resolved, bot, roles_service, state=shared_state)
    bot.admin_channel_service = admin_channel_service
    channel_service, mod_roles_service = _build_mod_provisioning(resolved, bot, registry, state=shared_state)
    bot.channel_service = channel_service
    bot.mod_roles_service = mod_roles_service
    bot.permission_service = _build_permission_service(resolved, bot, mod_roles_service, status.bot_admins)

    from kingdoms.discord.admin import register_admin_command
    from kingdoms.discord.live import register_live_commands
    from kingdoms.discord.registration import register_registration_command
    from kingdoms.discord.status import register_status_command

    guild_id = resolved.sync_guild_id.strip()
    sync_target = f"guild {guild_id}" if guild_id.isdigit() else "global"
    register_status_command(
        bot.tree, status, sync_target=sync_target, logs_service=bot.logs_service, catalog=bot.messages
    )
    register_live_commands(bot.tree, catalog=bot.messages)
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
    register_home_command(bot.tree, bot.home_service, catalog=bot.messages)
    bot.staff_service = _build_staff_service(resolved, bot, admin_channel_service)
    if bot.staff_service is not None:
        from kingdoms.discord.staff import register_staff_surface

        register_staff_surface(bot.tree, bot)
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
    from kingdoms.core.services.mod_entrypoint import register_mod
    from kingdoms.discord.wiring import set_guild_access_platform

    set_guild_access_platform(games, tuple(registry.enabled()))
    for mod_name in registry.enabled():
        register_mod(bot, resolved, mod_name)
    return bot


def _start_live_dashboard(bot: KingdomsBot) -> asyncio.Task[None] | None:
    """Run the live dashboard refresh loop when its stack is wired (#147)."""
    from kingdoms.core.rpc.live import LiveClient
    from kingdoms.discord.live import start_live_dashboard_refresh
    from kingdoms.discord.messages_platform import build_message_registry

    core_uri = os.environ.get("CORE_URI", "")
    registry = build_message_registry()
    if not core_uri or registry is None:
        return None

    async def _run() -> None:
        await start_live_dashboard_refresh(bot, LiveClient(core_uri), registry)

    return asyncio.create_task(_run())


def _build_staff_database(config: BotConfig) -> Any | None:
    """Wire the staff persistence (Mongo); None unwired."""
    if not config.mongo_uri:
        return None
    try:
        from kingdoms.core.models.db import get_async_database
        from kingdoms.discord.staff_platform import MongoStaffDatabase

        return MongoStaffDatabase(get_async_database())
    except Exception:
        return None


def _build_staff_service(
    config: BotConfig,
    bot: KingdomsBot,
    admin_channel_service: AdminChannelService | None,
) -> Any | None:
    """Wire the staff service: persistence + the admin-channel notice seam."""
    database = _build_staff_database(config)
    if database is None:
        return None
    from kingdoms.core.services.staff import StaffEvents, StaffService
    from kingdoms.discord.staff import build_staff_notice

    class _AdminNoticeEvents(StaffEvents):
        """Deliver the staff notices to the guild's admin channel."""

        async def notify_admins(self, message: str, payload: dict[str, Any]) -> None:
            """Forward an admin notification into the guild admin salon."""
            guild_id = str(payload.get("guild_id", ""))
            if not guild_id or admin_channel_service is None:
                return
            user_id = str(payload.get("user_id", ""))
            mod = str(payload.get("mod", ""))
            if payload.get("kind") == "staff.applied" and user_id:
                await admin_channel_service.deliver(
                    guild_id,
                    build_staff_notice(mod, user_id, str(payload.get("message", ""))),
                    tuple(bot.status_service.bot_admins),
                )

    return StaffService(database, _AdminNoticeEvents())


def _build_home_channel_service(
    config: BotConfig,
    bot: KingdomsBot,
) -> Any | None:
    """Wire the 🏛-kingdoms-home managed channel (cache-aside like the admin channel).

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
            name="🏛-kingdoms-home",
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


async def _maintain_pinned_home_menu(bot: KingdomsBot) -> None:
    """Keep the pinned home menu alive in every guild (self-healing)."""
    from kingdoms.discord.home import ensure_pinned_home_menu

    await asyncio.sleep(5)
    while True:
        for guild in list(bot.guilds):
            try:
                await ensure_pinned_home_menu(bot, str(guild.id))
            except Exception:
                logger.warning("PINNED HOME MENU check failed (guild %s) — best-effort", guild.id, exc_info=True)
        await asyncio.sleep(PINNED_MENU_CHECK_INTERVAL)


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
