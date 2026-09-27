"""Bot assembly: builds the runnable Kingdoms bot (kingdoms-services#12).

The factory wires, in order: Discord client + command tree, core services
(registry, status), the ``/status`` command, and — as their issues land —
the platform adapter and the core service trio (StateService,
WorkflowEngine, ChannelService). Everything that is not wired yet is
optional, so the bot merges incrementally while ``--preflight`` stays
green in CI (re-scoping note on kingdoms-services#12).

The bot class itself is thin: no manual interaction dispatch (CommandTree
and discord.py Views already route), platform logic stays in
``DiscordPlatform``, and the ``KINGDOMS_BOT_READY`` log contract of the
smoke CI is preserved (kingdoms-services#34).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands

from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.channel import ChannelService
from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
from kingdoms.core.services.permissions import PermissionService
from kingdoms.core.services.roles import ModRolesService, RolesService
from kingdoms.core.services.status import StatusService, parse_bot_admins
from kingdoms.discord.announce import AnnounceConfig, announce_startup
from kingdoms.discord.commands_i18n import CatalogTranslator
from kingdoms.discord.error_report import report_guild_error, report_interaction_error

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
        if announce_enabled:
            self._provision_task = asyncio.create_task(self._provision_default_channels())
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

    async def _provision_mods(
        self,
        guild_id: str,
        channel_service: ChannelService,
        mod_roles_service: ModRolesService,
        registry: ModRegistry,
    ) -> None:
        """Provision channels and roles for every enabled mod (best-effort, idempotent)."""
        for mod_name, definition in registry.enabled().items():
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
                logger.warning(
                    "MOD %s provisioning failed (guild %s) — best-effort", mod_name, guild_id, exc_info=True
                )

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Report app-command failures: bot-logs (guild) or the DM itself."""
        exc = error.__cause__ if error.__cause__ is not None else error
        logger.exception("APP COMMAND FAILED", exc_info=error)
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
        await super().close()


def create_bot(config: BotConfig | None = None) -> KingdomsBot:
    """Build the Kingdoms bot: client, tree, core services and commands."""
    resolved = config or BotConfig.from_env()
    registry = ModRegistry(load_mod_definitions(Path(resolved.config_dir)))
    status = StatusService(
        registry=registry,
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
    )
    bot = KingdomsBot(config=resolved, status=status, registry=registry)
    bot.logs_service = _build_log_service(resolved, bot)
    roles_service = _build_roles_service(resolved, bot)
    admin_channel_service = _build_admin_channel_service(resolved, bot, roles_service)
    bot.admin_channel_service = admin_channel_service
    channel_service, mod_roles_service = _build_mod_provisioning(resolved, bot, registry)
    bot.channel_service = channel_service
    bot.mod_roles_service = mod_roles_service
    bot.permission_service = _build_permission_service(resolved, bot, mod_roles_service, status.bot_admins)

    from kingdoms.discord.admin import register_admin_command
    from kingdoms.discord.status import register_status_command

    guild_id = resolved.sync_guild_id.strip()
    sync_target = f"guild {guild_id}" if guild_id.isdigit() else "global"
    register_status_command(
        bot.tree, status, sync_target=sync_target, logs_service=bot.logs_service, catalog=bot.messages
    )
    register_admin_command(
        bot.tree,
        bot_admins=status.bot_admins,
        logs_service=bot.logs_service,
        roles_service=roles_service,
        catalog=bot.messages,
        admin_channel_service=admin_channel_service,
    )
    return bot


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


def _build_roles_service(config: BotConfig, bot: KingdomsBot) -> RolesService | None:
    """Wire the Discord platform seam + the shared Redis state into RolesService.

    Returns None when Redis is not configured (unit tests, local runs):
    the runtime guards degrade to BOT_ADMINS + guild administrators.
    """
    if not config.redis_uri:
        return None
    try:
        from kingdoms.core.services.state import StateService
        from kingdoms.discord.roles_platform import DiscordRolesPlatform

        state = StateService(redis_uri=config.redis_uri)
        return RolesService(platform=DiscordRolesPlatform(bot), cache=state)
    except Exception:
        logger.exception("ROLES SERVICE WIRING FAILED — runtime role checks degrade")
        return None


def _build_mod_provisioning(
    config: BotConfig,
    bot: KingdomsBot,
    registry: ModRegistry,
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
        state = StateService(redis_uri=config.redis_uri)
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
            state=StateService(redis_uri=config.redis_uri),
        )
    except Exception:
        logger.exception("ADMIN CHANNEL SERVICE WIRING FAILED — admin messages degrade")
        return None


def _build_log_service(config: BotConfig, bot: KingdomsBot) -> LogService | None:
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

        state = StateService(redis_uri=config.redis_uri or None)
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
