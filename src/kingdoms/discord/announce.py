"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → adoption → creation, admin-only by default).

One deploy identity, one rendering: the announcement is a Components
V2 layout built through the UI SDK (:mod:`kingdoms.discord.ui`).
Per the SDK navigation rules, text blocks carry plain labels only
(links and line breaks do not render in V2 text) — every artifact is
a link button, and the identity rides on the buttons themselves: the
PR number, the commit sha7, the truncated docker tag, the branch.
Emojis replace text labels on buttons where the meaning is clear.

Sections: Services (source identity + commit date, build artifacts +
build date), Infra (state identity + commit date, deployment run +
run date), Bot (uptime, admins, games, mods, gateway latency) — each
artifact's timestamp sits directly under it. The announcement doubles
as a machine-readable deployment signal: the frozen footer line
(``kingdoms-deploy <env>``) rides in a TextDisplay sub-text, readable
back through the Discord REST API by the kingdoms-infra post-deploy
battery (kingdoms-infra#78). The full identity already rides in the
body; the footer only repeats the environment — the one thing the
battery cannot infer from the message alone. The footer format is
frozen: breaking changes need a battery-side update first.

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
from typing import Any

import discord
import yaml

from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.status import StatusService
from kingdoms.discord.deploy_render import (
    INFRA_REPO_URL,
    PACKAGE_URL,
    SERVICES_REPO_URL,
    docker_tag,
    relative_time,
    sha7_of,
    short_tag,
)
from kingdoms.discord.ui import (
    BLURPLE,
    Button,
    Container,
    Row,
    Section,
    Separator,
    Text,
    UILayout,
)

ROLLBACK_WORKFLOW_URL = f"{INFRA_REPO_URL}/actions/workflows/rollback.yml"

logger = logging.getLogger("kingdoms.bot.announce")

FOOTER_PREFIX = "kingdoms-deploy"

_VERSION_BUTTON_KINDS = {"pr": "pull_request_button", "main": "commit_button", "release": "release_button"}


ANNOUNCEMENT_HEADER = "🚀"
STATUS_LINK_LABEL = "/status"


@dataclass(frozen=True, slots=True)
class AnnounceConfig:
    """Announcement configuration (locale only — channels are owned by LogService)."""

    locale: str = "en"
    config_dir: Path = Path("config")


def deploy_footer(status: StatusService, env: str = "") -> str:
    """Render the machine-readable footer line (frozen format).

    ``kingdoms-deploy <env>`` — the identity (image, kind, ref, run)
    already rides in the body buttons, so the footer only repeats the
    environment, the one field the battery (kingdoms-infra#78) cannot
    infer from the message; the pinned image the battery compares
    against comes from the state file, not from the message.
    """
    return f"{FOOTER_PREFIX} {env}".rstrip()


def _release_section(status: StatusService, catalog: dict[str, str]) -> Section | Text | None:
    """Build the headline: what is deployed (PR title or version) + its button.

    Releases headline as ``Version <ref>`` (the vX.Y.Z tag), not the
    image label; PRs keep their title. A Section with the PR/Commit/
    Release button as accessory when the pipeline provides a URL; a
    plain Text fallback (title only); None when there is nothing to
    headline.
    """
    title = status.deploy_pr_title or ""
    if status.deploy_kind == "release" and status.deploy_ref:
        title = f"{catalog['version_label']} {status.deploy_ref}"
    if not title:
        return None
    if status.deploy_url:
        identity = status.deploy_ref or status.deploy_label
        return Section(Text(f"## {title}"), button=Button(f"🔗 {identity}", status.deploy_url))
    return Text(f"## {title}")


def _line(label: str, value: str, ts: str = "") -> str:
    """One identity line: label + value, timestamp appended when set."""
    rendered = f"{label} `{value}`" if value else label
    return f"{rendered} {ts}".rstrip() if ts else rendered


def _services_identity_text(status: StatusService, catalog: dict[str, str]) -> Text | None:
    """Build the source identity lines: branch and commit + commit date."""
    sha = sha7_of(status.deploy_tree_url)
    lines = [
        line
        for line in (
            _line(f"🌿 {catalog['branch_label']}", status.deploy_branch),
            _line(f"🔧 {catalog['commit_label']}", sha, relative_time(status.deploy_commit_ts)),
        )
        if line
    ]
    return Text("\n".join(lines)) if lines else None


def _services_artifact_row(status: StatusService, catalog: dict[str, str]) -> list[Button]:
    """Build the Services artifact buttons: branch, commit, files, version."""
    buttons: list[Button] = []
    if status.deploy_branch:
        buttons.append(Button(f"🌿 {status.deploy_branch}", f"{SERVICES_REPO_URL}/tree/{status.deploy_branch}"))
    sha = sha7_of(status.deploy_tree_url)
    if sha:
        buttons.append(Button(f"🔧 {sha}", f"{SERVICES_REPO_URL}/commit/{sha}"))
        if status.deploy_tree_url:
            buttons.append(Button(f"🗂️ {catalog['files_label']}", status.deploy_tree_url))
    if status.deploy_url and not _release_section(status, catalog):
        identity = status.deploy_ref or status.deploy_label
        buttons.append(Button(f"🔗 {identity}", status.deploy_url))
    return buttons


def _services_build_row(status: StatusService) -> list[Button]:
    """Build the build buttons: pipeline run + package image."""
    buttons: list[Button] = []
    if status.deploy_run_url:
        buttons.append(Button("🚦", status.deploy_run_url))
    if status.deploy_image:
        buttons.append(Button("📦", PACKAGE_URL))
    return buttons


def _services_blocks(status: StatusService, catalog: dict[str, str]) -> list[object]:
    """Build the Services blocks: identity + timestamps + buttons.

    The source identity (branch, commit) and the build identity
    (docker tag) each carry their own timestamp on the line directly
    under them — text blocks carry no links, every artifact is a link
    button with its identity as the label.
    """
    blocks: list[object] = [Text(f"**{catalog['services_label']}**")]
    identity = _services_identity_text(status, catalog)
    if identity is not None:
        blocks.append(identity)
    first = _services_artifact_row(status, catalog)
    if first:
        blocks.append(Row(*first))
    tag = docker_tag(status.deploy_image or status.deploy_label)
    if tag:
        blocks.append(Text(_line(f"📦 {catalog['image_label']}", short_tag(tag), relative_time(status.deploy_ts))))
    build = _services_build_row(status)
    if build:
        blocks.append(Row(*build))
    return blocks


def _infra_blocks(status: StatusService, catalog: dict[str, str]) -> list[object]:
    """Build the Infra blocks: state identity + deployment, same layout.

    The state commit carries its own timestamp, distinct from the
    deployment run's — one line per artifact, timestamp under it.
    """
    blocks: list[object] = [Text(f"**{catalog['infra_label']}**")]
    branch, _, sha = status.deploy_infra_label.partition("@")
    sha7 = sha[:7] if sha else ""
    commit_ts = relative_time(status.deploy_infra_commit_ts)
    if branch or sha7:
        blocks.append(
            Text(
                "\n".join(
                    line
                    for line in (
                        _line(f"🌿 {catalog['branch_label']}", branch),
                        _line(f"🔧 {catalog['commit_label']}", sha7, commit_ts),
                    )
                    if line
                )
            )
        )
    row: list[Button] = []
    if branch:
        row.append(Button(f"🌿 {branch}", f"{INFRA_REPO_URL}/tree/{branch}"))
    if sha:
        row.append(Button(f"🔧 {sha7}", f"{INFRA_REPO_URL}/commit/{sha}"))
    if status.deploy_infra_url:
        row.append(Button(f"🗂️ {catalog['files_label']}", status.deploy_infra_url))
    row.append(Button(f"⏪ {catalog['rollback_label']}", ROLLBACK_WORKFLOW_URL))
    if row:
        blocks.append(Row(*row))
    run_ts = relative_time(status.deploy_run_ts)
    if status.deploy_run_url:
        job_id = status.deploy_run_url.rstrip("/").rsplit("/", 1)[-1]
        label = f"#{status.deploy_run_number}" if status.deploy_run_number else catalog["deployment_label"]
        job_line = f"{label} (`{job_id}`)" if job_id.isdigit() else label
        blocks.append(Text(_line(f"🚀 {catalog['deployment_label']}", job_line, run_ts)))
        blocks.append(
            Row(
                Button(f"🚀 {label}", status.deploy_run_url),
            )
        )
    return blocks


def _bot_blocks(status: StatusService, catalog: dict[str, str], latency_ms: int | None) -> list[object]:
    """Build the Bot blocks: uptime, admins, games, mods, latency.

    Same identity as /status: the uptime renders through the shared
    human helper, admins as Discord mentions, mods and games as their
    ids, the gateway latency as milliseconds (None before the first
    heartbeat — the announcement fires right at startup).
    """
    lines: list[str] = [f"- {catalog['uptime_label']}: {_human_uptime(status.uptime_seconds())}"]
    admins = status.bot_admins
    if admins:
        rendered = " ".join(f"<@{uid}>" for uid in admins)
        lines.append(f"- {catalog['admins_label']}: {rendered}")
    games = status.games()
    rendered_games = ", ".join(games) if games else catalog["none_label"]
    lines.append(f"- {catalog['games_label']}: {rendered_games}")
    mods = status.enabled_mods()
    rendered_mods = ", ".join(mods) if mods else catalog["none_label"]
    lines.append(f"- {catalog['mods_label']}: {rendered_mods}")
    if latency_ms is not None:
        lines.append(f"- {catalog['latency_label']}: {latency_ms} ms")
    blocks: list[object] = [Text(f"**{catalog['bot_label']}**"), Text("\n".join(lines))]
    return blocks


def _human_uptime(seconds: float) -> str:
    """Render an uptime duration as a compact human string (/status parity)."""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


def _gateway_latency(bot: discord.Client) -> int | None:
    """Gateway latency in ms; None before the first heartbeat or on NaN."""
    latency = bot.latency
    if latency is None or latency != latency or latency == float("inf") or latency < 0:
        return None
    return round(latency * 1000)


def build_announcement_layout(
    status: StatusService,
    config: AnnounceConfig,
    env: str = "",
    thumbnail_url: str = "",
    latency_ms: int | None = None,
    extra_blocks: list[object] | None = None,
) -> discord.ui.LayoutView:
    """Build the Components V2 announcement: headline + sections + footer.

    Layout: one accent Container holding a header TextDisplay (title +
    env badge), the headline section (the deployed PR title or version
    with its button accessory), the Services blocks, a Separator, the
    Infra blocks, a Separator, the Bot blocks, and the machine-readable
    footer as sub-text (frozen format, ``kingdoms-deploy <env>``).
    Same identity as /status, adapted to the V2 navigation rules.
    """
    catalog = _load_catalog(config.locale, config.config_dir)
    header = f"# {ANNOUNCEMENT_HEADER} {catalog['title']}"
    if env:
        header = f"{header}\n-# `{env}`"
    container = Container(accent=BLURPLE).add(Text(header))
    release = _release_section(status, catalog)
    if release is not None:
        container = container.add(release)
    container = container.add(*_bot_blocks(status, catalog, latency_ms))
    container = container.add(Separator())
    container = container.add(*_services_blocks(status, catalog))
    container = container.add(Separator())
    container = container.add(*_infra_blocks(status, catalog))
    if extra_blocks:
        container = container.add(Separator())
        container = container.add(*extra_blocks)
    container = container.add(Separator())
    container = container.add(Text(f"-# {STATUS_LINK_LABEL} · {deploy_footer(status, env=env)}"))
    return UILayout().add(container).build()


async def announce_startup(
    bot: discord.Client,
    status: StatusService,
    config: AnnounceConfig,
    logs_service: LogService | None = None,
    deploy_env: str = "",
    enabled: bool = True,
    thumbnail_url: str = "",
    locale_resolver: Any = None,
) -> None:
    """Post the deployment announcement per guild in its bot logs channel.

    ``locale_resolver`` (an async ``guild_id -> locale`` callable, the
    LogService's ``get_locale``) localizes per guild when provided —
    each guild's language choice (managed through /admin) applies to
    its own announcement; without it the AnnounceConfig locale stands.
    """
    if not enabled:
        logger.info("STARTUP ANNOUNCEMENT DISABLED (KINGDOMS_ANNOUNCE_ENABLED=0)")
        return
    if logs_service is None:
        logger.info("STARTUP ANNOUNCEMENT SKIPPED: no LogService wired (local run?)")
        return
    latency_ms = _gateway_latency(bot)
    for guild in bot.guilds:
        locale = config.locale
        if locale_resolver is not None:
            try:
                locale = await locale_resolver(str(guild.id))
            except Exception:
                logger.warning("guild locale lookup failed (guild %s) — falling back", guild.id)
        guild_config = AnnounceConfig(locale=locale, config_dir=config.config_dir)
        layout = build_announcement_layout(
            status, guild_config, env=deploy_env, thumbnail_url=thumbnail_url, latency_ms=latency_ms
        )
        event = LifecycleEvent(kind="start", message="", layout=layout, footer=deploy_footer(status, env=deploy_env))
        await logs_service.log_event(str(guild.id), event)
        logger.info("STARTUP ANNOUNCEMENT SENT to guild %s (locale=%s)", guild.id, locale)


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
        "bot_label": "Bot",
        "version_label": "Version",
        "branch_label": "Branch",
        "commit_label": "Commit",
        "image_label": "Image",
        "deployment_label": "Deployment",
        "uptime_label": "Uptime",
        "admins_label": "Admins",
        "games_label": "Games",
        "mods_label": "Mods",
        "latency_label": "Latency",
        "none_label": "*(none configured)*",
        "files_label": "Files",
        "rollback_label": "Rollback",
    }
    return {key: str(section.get(key, default)) for key, default in defaults.items()}
