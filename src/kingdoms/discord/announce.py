"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → creation, admin-only by default).

One deploy identity, one rendering: the announcement embed reuses the
exact /status helpers (``format_version`` → ``format_services_section``
and ``format_deploy``) instead of a parallel message format — Services
and Infra compact linked lines, localized title and environment badge.

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
from kingdoms.core.services.status import StatusService, format_version
from kingdoms.discord.status import format_deploy, format_services_section

logger = logging.getLogger("kingdoms.bot.announce")

FOOTER_PREFIX = "kingdoms-deploy"

ANNOUNCE_COLOR = 0x5865F2


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


def build_announcement_embed(
    status: StatusService,
    config: AnnounceConfig,
    env: str = "",
) -> discord.Embed:
    """Build the localized announcement embed from the /status deploy lines.

    Same helpers as the /status command (one code path): the Services
    field (version line, commit/files, image) and the Infra field (state
    branch/commit, deployment run) render as compact labeled links —
    never a duplicated prose format.
    """
    catalog = _load_catalog(config.locale, config.config_dir)
    embed = discord.Embed(title=catalog["title"], color=ANNOUNCE_COLOR)
    if env:
        embed.description = f"`{env}`"
    embed.add_field(
        name=catalog["services_label"],
        value=format_services_section(
            format_version(
                status.deploy_label,
                status.deploy_url,
                kind=status.deploy_kind,
                ref=status.deploy_ref,
                tree_url=status.deploy_tree_url,
                ts=status.deploy_ts,
                pr_title=status.deploy_pr_title,
            ),
            status.deploy_image,
            status.deploy_kind,
            status.deploy_url,
            branch=status.deploy_branch,
            tree_url=status.deploy_tree_url,
            ts=status.deploy_ts,
        ),
        inline=True,
    )
    embed.add_field(
        name=catalog["infra_label"],
        value=format_deploy(
            status.deploy_run_url,
            status.deploy_url,
            status.deploy_infra_label,
            status.deploy_infra_url,
            status.deploy_run_number,
            status.deploy_run_ts,
        ),
        inline=True,
    )
    return embed


def announcement_fallback_text(status: StatusService, env: str = "") -> str:
    """Plain-text fallback when the platform rejects embeds."""
    version = status.deploy_label or status.deploy_image or "unknown"
    text = f"Version {version}"
    if env:
        text = f"{text} · {env}"
    return text


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
    embed = build_announcement_embed(status, config, env=deploy_env)
    fallback = announcement_fallback_text(status, env=deploy_env)
    event = LifecycleEvent(kind="start", message=fallback, embed=embed, footer=deploy_footer(status, env=deploy_env))
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
        "services_label": "Services",
        "infra_label": "Infra",
    }
    return {key: str(section.get(key, default)) for key, default in defaults.items()}
