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

from kingdoms.core.services.mod_registry import ModRegistry, load_mod_definitions
from kingdoms.core.services.status import StatusService, parse_bot_admins

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
    sync_guild_id: str = ""
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
            sync_guild_id=env.get("CICD_GUILD_ID", ""),
            log_level=env.get("LOG_LEVEL", "INFO"),
        )


class KingdomsBot(discord.Client):
    """Discord client with a command tree; core services are attached, not inherited."""

    def __init__(self, config: BotConfig, status: StatusService) -> None:
        """Create the client, the command tree and attach the services."""
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.config = config
        self.status_service = status
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
    )
    bot = KingdomsBot(config=resolved, status=status)
    from kingdoms.discord.status import register_status_command

    guild_id = resolved.sync_guild_id.strip()
    sync_target = f"guild {guild_id}" if guild_id.isdigit() else "global"
    register_status_command(bot.tree, status, sync_target=sync_target)
    return bot


def run_bot(config: BotConfig | None = None) -> None:
    """Build the bot and block until the gateway connection ends."""
    resolved = config or BotConfig.from_env()
    bot = create_bot(resolved)
    bot.run(resolved.discord_token, log_handler=None)


def _bot_version() -> str:
    """Return the installed kingdoms version."""
    from kingdoms import __version__

    return __version__
