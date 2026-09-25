"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → adoption → creation, admin-only by default).

One deploy identity, one rendering: the announcement is a Components
V2 layout (Container, Section with a thumbnail accessory, Separator)
reusing the exact /status helpers (``format_version`` →
``format_services_section`` and ``format_deploy``) — never a parallel
message format. The layout is pure display (no interactive items), so
no view timeout or dispatch wiring is involved.

The announcement doubles as a machine-readable deployment signal: the
frozen footer line (``kingdoms-deploy env=<env> image=<label>
kind=<kind> ref=<ref> run=<run-url>``) rides in a TextDisplay
sub-text, readable back through the Discord REST API by the
kingdoms-infra post-deploy battery (kingdoms-infra#78). The footer
format is frozen: breaking changes need a battery-side update first.

Announcements can be silenced entirely (``KINGDOMS_ANNOUNCE_ENABLED=0``)
— the CI/CD smoke bot uses this to boot against the real gateway
without posting startup messages in the shared guilds.

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

ANNOUNCEMENT_HEADER = "🚀"
STATUS_LINK_LABEL = "/status"


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


class AnnouncementLayout(discord.ui.LayoutView):
    """The Components V2 deployment announcement layout.

    One accent Container: header TextDisplay (title + env badge), the
    Services/Infra /status lines in a Section with a thumbnail
    accessory (plain TextDisplay without a thumbnail), a Separator,
    and the machine-readable footer as sub-text.
    """

    def __init__(
        self,
        header: str,
        services_line: str,
        infra_line: str,
        catalog: dict[str, str],
        footer: str,
        thumbnail_url: str = "",
    ) -> None:
        super().__init__(timeout=None)
        section_text: discord.ui.TextDisplay[AnnouncementLayout] = discord.ui.TextDisplay(
            f"{services_line}\n\n{infra_line}"
        )
        body: discord.ui.Item[AnnouncementLayout] = section_text
        if thumbnail_url:
            accessory: discord.ui.Thumbnail[AnnouncementLayout] = discord.ui.Thumbnail(thumbnail_url)
            body = discord.ui.Section(section_text, accessory=accessory)
        container: discord.ui.Container[AnnouncementLayout] = discord.ui.Container(
            discord.ui.TextDisplay(header),
            body,
            discord.ui.Separator(),
            discord.ui.TextDisplay(f"-# {STATUS_LINK_LABEL} · {footer}"),
            accent_colour=ANNOUNCE_COLOR,
        )
        self.add_item(container)


def build_announcement_layout(
    status: StatusService,
    config: AnnounceConfig,
    env: str = "",
    thumbnail_url: str = "",
) -> discord.ui.LayoutView:
    """Build the Components V2 announcement, reusing the /status deploy lines.

    Layout: one accent Container holding a header TextDisplay (title +
    env badge), a Section whose text stacks the Services and Infra
    /status lines with a thumbnail accessory, a Separator, and the
    machine-readable footer as sub-text. Same identity rendering as
    the /status command — one code path.
    """
    catalog = _load_catalog(config.locale, config.config_dir)
    services = format_services_section(
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
    )
    infra = format_deploy(
        status.deploy_run_url,
        status.deploy_url,
        status.deploy_infra_label,
        status.deploy_infra_url,
        status.deploy_run_number,
        status.deploy_run_ts,
    )
    header = f"# {ANNOUNCEMENT_HEADER} {catalog['title']}"
    if env:
        header = f"{header}\n-# `{env}`"
    services_line = f"**{catalog['services_label']}**\n{services}"
    infra_line = f"**{catalog['infra_label']}**\n{infra}"
    layout = AnnouncementLayout(
        header, services_line, infra_line, catalog, deploy_footer(status, env=env), thumbnail_url
    )
    return layout


async def announce_startup(
    bot: discord.Client,
    status: StatusService,
    config: AnnounceConfig,
    logs_service: LogService | None = None,
    deploy_env: str = "",
    enabled: bool = True,
    thumbnail_url: str = "",
) -> None:
    """Post the deployment announcement per guild in its bot logs channel."""
    if not enabled:
        logger.info("STARTUP ANNOUNCEMENT DISABLED (KINGDOMS_ANNOUNCE_ENABLED=0)")
        return
    if logs_service is None:
        logger.info("STARTUP ANNOUNCEMENT SKIPPED: no LogService wired (local run?)")
        return
    layout = build_announcement_layout(status, config, env=deploy_env, thumbnail_url=thumbnail_url)
    event = LifecycleEvent(kind="start", message="", layout=layout, footer=deploy_footer(status, env=deploy_env))
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
