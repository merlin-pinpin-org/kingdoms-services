"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → creation, admin-only by default). The content reuses
the ``/status`` deploy identity (``KINGDOMS_DEPLOY_*``): no dedicated
injection pipeline.

The announcement doubles as a machine-readable deployment signal: the
frozen footer line (``kingdoms-deploy env=<env> image=<label>
kind=<kind> ref=<ref> run=<run-url>``) lets the kingdoms-infra
post-deploy battery (kingdoms-infra#78) read it back through the
Discord REST API and assert that the running bot announces what the
pinned state says. The footer format is frozen: breaking changes need
a battery-side update first.

Without a LogService (local runs, unit tests), the announcement
degrades to nothing — silently skipped. Delivery is best-effort either
way: startup readiness never depends on message delivery.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import discord
import yaml

from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.status import StatusService

logger = logging.getLogger("kingdoms.bot.announce")

FOOTER_PREFIX = "kingdoms-deploy"


@dataclass(frozen=True, slots=True)
class AnnounceConfig:
    """Announcement configuration (locale only — channels are owned by LogService)."""

    locale: str = "en"
    config_dir: Path = Path("config")


def deploy_footer(status: StatusService, env: str = "") -> str:
    """Render the machine-readable footer line (frozen format).

    ``kingdoms-deploy env=<env> image=<label> kind=<kind> ref=<ref> run=<run-url>``
    — empty fields render empty so the line stays greppable; the battery
    parses ``key=value`` pairs and compares against the pinned state.
    """
    fields = (
        ("env", env),
        ("image", status.deploy_label or status.deploy_image),
        ("kind", status.deploy_kind),
        ("ref", status.deploy_ref),
        ("run", status.deploy_run_url or status.deploy_url),
    )
    rendered = " ".join(f"{key}={value or ''}" for key, value in fields)
    return f"{FOOTER_PREFIX} {rendered}".rstrip()


def build_announcement_message(status: StatusService, config: AnnounceConfig, env: str = "") -> str:
    """Build the localized announcement content with the identity footer."""
    catalog = _load_catalog(config.locale, config.config_dir)
    version = status.deploy_label or status.deploy_image or "unknown"
    lines = [f"**{catalog['title']}**", catalog["body"], f"**{catalog['version_label']}**: {version}"]
    if env:
        lines.append(f"**{catalog['environment_label']}**: `{env}`")
    url = status.deploy_url or status.deploy_run_url
    if url:
        label = catalog["deployment_label"]
        lines.append(f"**{label}**: <{url}>")
    return "\n".join(lines)


async def announce_startup(
    bot: discord.Client,
    status: StatusService,
    config: AnnounceConfig,
    logs_service: LogService | None = None,
    deploy_env: str = "",
) -> None:
    """Post the deployment announcement per guild in its bot logs channel."""
    if logs_service is None:
        logger.info("STARTUP ANNOUNCEMENT SKIPPED: no LogService wired (local run?)")
        return
    message = build_announcement_message(status, config, env=deploy_env)
    event = LifecycleEvent(
        kind="start",
        message=message,
        footer=deploy_footer(status, env=deploy_env),
    )
    for guild in bot.guilds:
        await logs_service.log_event(str(guild.id), event)
        logger.info("STARTUP ANNOUNCEMENT SENT to guild %s", guild.id)


def _load_catalog(locale: str, config_dir: Path) -> dict[str, str]:
    """Load the announce strings for a locale (en fallback)."""
    path = config_dir / "locales" / f"{locale}.yaml"
    try:
        with open(path, encoding="utf-8") as fh:
            catalog = yaml.safe_load(fh) or {}
    except OSError:
        catalog = {}
    section = catalog.get(locale, {}).get("announce")
    if not isinstance(section, dict):
        if locale != "en":
            return _load_catalog("en", config_dir)
        section = {}
    defaults = {
        "title": "Kingdoms — Deployment",
        "body": "The bot is live on the gateway. Deployed version below.",
        "version_label": "Version",
        "environment_label": "Environment",
        "deployment_label": "Deployment",
    }
    return {key: str(section.get(key, default)) for key, default in defaults.items()}
