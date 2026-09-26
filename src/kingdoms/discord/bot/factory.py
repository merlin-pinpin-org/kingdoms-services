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

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import discord
from discord import app_commands

from kingdoms.core.services.admin_channel import AdminChannelService
from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
from kingdoms.core.services.roles import RolesService
from kingdoms.core.services.status import StatusService, parse_bot_admins
from kingdoms.discord.announce import AnnounceConfig, announce_startup
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

    def __init__(self, config: BotConfig, status: StatusService, logs: LogService | None = None) -> None:
        """Create the client, the command tree and attach the services."""
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.status_service = status
        self.logs_service = logs
        self.tree = app_commands.CommandTree(self)
        self._synced = False

    async def on_ready(self) -> None:
        """Log the ready marker asserted by smoke CI, then sync commands once."""
        logger.info(
            "%s version=%s user=%s guilds=%d",
            READY_LOG_LINE,
            _bot_version(),
            self.user,
            len(self.guilds),
        )
        await announce_startup(
            self,
            self.status_service,
            AnnounceConfig(
                locale=self.config.announce_locale,
                config_dir=self.config.config_dir,
            ),
            logs_service=self.logs_service,
            deploy_env=self.config.deploy_env,
            enabled=self.config.announce_enabled.strip().lower() not in {"0", "false", "no"},
            thumbnail_url=self.user.display_avatar.url if self.user else "",
            locale_resolver=self.logs_service.get_locale if self.logs_service is not None else None,
            commands=self.tree.get_commands(),
        )
        self.tree.on_error = self.on_tree_error  # type: ignore[method-assign]
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
                event = LifecycleEvent(kind="stop", message="Bot shutting down.")
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
    bot = KingdomsBot(config=resolved, status=status)
    bot.logs_service = _build_log_service(resolved, bot)
    roles_service = _build_roles_service(resolved, bot)
    admin_channel_service = _build_admin_channel_service(resolved, bot, roles_service)
    from kingdoms.discord.admin import register_admin_command
    from kingdoms.discord.enrollment import register_enrollment_command
    from kingdoms.discord.status import register_status_command

    guild_id = resolved.sync_guild_id.strip()
    sync_target = f"guild {guild_id}" if guild_id.isdigit() else "global"
    register_status_command(bot.tree, status, sync_target=sync_target)
    register_admin_command(
        bot.tree,
        bot_admins=status.bot_admins,
        logs_service=bot.logs_service,
        roles_service=roles_service,
    )
    register_enrollment_command(
        bot.tree,
        bot_admins=status.bot_admins,
        roles_service=roles_service,
        admin_channel_service=admin_channel_service,
    )
    return bot


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
