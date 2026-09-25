"""Startup announcement: one message per gateway connection (kingdoms-services#52).

When the environment provides ``ANNOUNCE_CHANNEL_ID``, the bot posts a
single announcement in that channel on real gateway connection — the
game designer sees "the PR I asked for is now live" in Discord, without
watching GitHub Actions. The content reuses the ``/status`` deploy
identity (``KINGDOMS_DEPLOY_*``): no dedicated injection pipeline.

The announcement doubles as a machine-readable deployment signal: a
stable footer line (``KINGDOMS_DEPLOY_FOOTER``) carries the deployed
identity so the kingdoms-infra post-deploy battery (kingdoms-infra#78)
can read it back through the Discord REST API and assert that the
running bot announces what the pinned state says. The footer format is
frozen: breaking changes need a battery-side update first.

Delivery is best-effort: an unreachable channel or a missing permission
is logged, never a startup failure — readiness must not depend on
message delivery. The announcement is skipped silently when the channel
variable is absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import discord
import yaml

from kingdoms.core.services.status import StatusService

logger = logging.getLogger("kingdoms.bot.announce")

FOOTER_PREFIX = "kingdoms-deploy"


@dataclass(frozen=True, slots=True)
class AnnounceConfig:
    """Announcement configuration (environment-driven)."""

    channel_id: str = ""
    locale: str = "en"
    config_dir: Path = Path("config")

    @property
    def enabled(self) -> bool:
        """Announce only when a channel is configured."""
        return self.channel_id.strip().isdigit()


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


def build_announcement_embed(status: StatusService, config: AnnounceConfig, env: str = "") -> discord.Embed:
    """Build the localized announcement embed with the identity footer."""
    catalog = _load_catalog(config.locale, config.config_dir)
    version = status.deploy_label or status.deploy_image or "unknown"
    lines = [catalog["body"], f"**{catalog['version_label']}**: {version}"]
    if env:
        lines.append(f"**{catalog['environment_label']}**: `{env}`")
    url = status.deploy_url or status.deploy_run_url
    if url:
        label = catalog["deployment_label"]
        lines.append(f"**{label}**: <{url}>")
    embed = discord.Embed(title=catalog["title"], description="\n".join(lines))
    embed.set_footer(text=deploy_footer(status, env))
    return embed


async def announce_startup(bot: discord.Client, status: StatusService, config: AnnounceConfig) -> None:
    """Post the startup announcement; best-effort, never raises."""
    if not config.enabled:
        return
    channel_id = int(config.channel_id.strip())
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            logger.warning("ANNOUNCE_CHANNEL_ID %s is not a text channel: announcement skipped", channel_id)
            return
        await channel.send(embed=build_announcement_embed(status, config))
        logger.info("STARTUP ANNOUNCEMENT SENT to channel %s", channel_id)
    except Exception:
        logger.exception("STARTUP ANNOUNCEMENT FAILED (channel %s) — delivery is best-effort", channel_id)


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
