"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → adoption → creation, admin-only by default).

One deploy identity, one rendering: the announcement is a Components
V2 layout built through the UI SDK (:mod:`kingdoms.discord.ui`).
Per the SDK navigation rules, text blocks carry plain labels only
(links and line breaks do not render in V2 text) — every artifact
is a link button in an action row: Services (Branch, PR/Commit/
Release, the commit sha with its Commit/Files buttons, the build
pipeline and package image), Infra (state Branch/Commit/Files, the
deployment run). The layout is pure display (link buttons only), so
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
from kingdoms.core.services.status import StatusService
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

logger = logging.getLogger("kingdoms.bot.announce")

FOOTER_PREFIX = "kingdoms-deploy"

SERVICES_REPO_URL = "https://github.com/merlin-pinpin-org/kingdoms-services"
INFRA_REPO_URL = "https://github.com/merlin-pinpin-org/kingdoms-infra"
PACKAGE_URL = f"{SERVICES_REPO_URL}/pkgs/container/kingdoms-services"

_VERSION_BUTTON_KINDS = {"pr": "pull_request_button", "main": "commit_button", "release": "release_button"}


def _sha7(tree_url: str) -> str:
    """Extract the deployed commit sha from its tree URL."""
    return tree_url.rstrip("/").rsplit("/", 1)[-1][:7]

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


def _release_section(status: StatusService, catalog: dict[str, str]) -> Section | Text | None:
    """Build the headline: what is deployed (PR title or version) + its button.

    A Section with the PR/Commit/Release button as accessory when the
    pipeline provides a URL; a plain Text fallback (title only); None
    when there is nothing to headline.
    """
    title = status.deploy_pr_title or ""
    if not title and status.deploy_kind == "release" and status.deploy_ref:
        title = status.deploy_ref
    if not title:
        return None
    if status.deploy_url:
        key = _VERSION_BUTTON_KINDS.get(status.deploy_kind, "version_button")
        return Section(Text(f"## {title}"), button=Button(catalog[key], status.deploy_url))
    return Text(f"## {title}")


def _artifact_ref(branch: str, sha: str) -> str:
    """Render the branch@sha7 identity line shown above the buttons."""
    ref = "@".join(part for part in (branch, sha) if part)
    return f"`{ref}`" if ref else ""


def _services_blocks(status: StatusService, catalog: dict[str, str]) -> list[object]:
    """Build the Services blocks: labels + timestamps + link-button rows.

    The branch@sha7 identity sits above the artifact buttons; the job
    timestamps (build, deploy) are shown as relative times. Text blocks
    carry no links — every artifact is a link button.
    """
    blocks: list[object] = [Text(f"**{catalog['services_label']}**")]
    identity = _artifact_ref(status.deploy_branch, _sha7(status.deploy_tree_url))
    if identity:
        blocks.append(Text(identity))
    first: list[Button] = []
    if status.deploy_branch:
        first.append(Button(catalog["branch_button"], f"{SERVICES_REPO_URL}/tree/{status.deploy_branch}"))
    sha = _sha7(status.deploy_tree_url)
    if sha:
        first.append(Button(catalog["commit_button"], f"{SERVICES_REPO_URL}/commit/{sha}"))
        if status.deploy_tree_url:
            first.append(Button(catalog["files_button"], status.deploy_tree_url))
    if status.deploy_url and not _release_section(status, catalog):
        key = _VERSION_BUTTON_KINDS.get(status.deploy_kind, "version_button")
        first.append(Button(catalog[key], status.deploy_url))
    if first:
        blocks.append(Row(*first))
    times: list[str] = []
    if status.deploy_ts.strip().isdigit():
        times.append(f"{catalog['built_at_label']} <t:{status.deploy_ts.strip()}:R>")
    if times:
        blocks.append(Text("\n".join(times)))
    build = _build_row(status, catalog)
    if build:
        blocks.append(Row(*build))
    return blocks


def _build_row(status: StatusService, catalog: dict[str, str]) -> list[Button]:
    """Build the build row: pipeline run + package image buttons."""
    buttons: list[Button] = []
    if status.deploy_run_url:
        buttons.append(Button(catalog["pipeline_button"], status.deploy_run_url, "🚦"))
    if status.deploy_image:
        buttons.append(Button(catalog["image_button"], PACKAGE_URL, "📦"))
    return buttons


def _infra_section(status: StatusService, catalog: dict[str, str]) -> Section | Text:
    """Build the Infra headline: state branch@sha7 + the deployment button.

    A Section with the Deployment run button as accessory when there
    is a run URL; a plain Text fallback.
    """
    branch, _, sha = status.deploy_infra_label.partition("@")
    lines: list[str] = [f"**{catalog['infra_label']}**"]
    identity = _artifact_ref(branch, sha[:7] if sha else "")
    if identity:
        lines.append(identity)
    if status.deploy_run_ts.strip().isdigit():
        lines.append(f"{catalog['deployed_at_label']} <t:{status.deploy_run_ts.strip()}:R>")
    text = Text("\n".join(lines))
    if not status.deploy_run_url:
        return text
    run_id = f"#{status.deploy_run_number}" if status.deploy_run_number else ""
    button = Button(f"{catalog['deployment_button']} {run_id}".rstrip(), status.deploy_run_url, "🚀")
    return Section(text, button=button)


def _infra_blocks(status: StatusService, catalog: dict[str, str]) -> list[object]:
    """Build the Infra blocks: the headline section + the artifact row."""
    blocks: list[object] = [_infra_section(status, catalog)]
    branch, _, sha = status.deploy_infra_label.partition("@")
    row: list[Button] = []
    if branch:
        row.append(Button(catalog["branch_button"], f"{INFRA_REPO_URL}/tree/{branch}"))
    if sha:
        row.append(Button(catalog["commit_button"], f"{INFRA_REPO_URL}/commit/{sha}"))
    if status.deploy_infra_url:
        row.append(Button(catalog["files_button"], status.deploy_infra_url))
    if row:
        blocks.append(Row(*row))
    return blocks


def build_announcement_layout(
    status: StatusService,
    config: AnnounceConfig,
    env: str = "",
    thumbnail_url: str = "",
) -> discord.ui.LayoutView:
    """Build the Components V2 announcement: headline + action rows.

    Layout: one accent Container holding a header TextDisplay (title +
    env badge), the headline section (the deployed PR title or version
    with its button accessory), the Services blocks (branch@sha7
    identity above the artifact buttons, job timestamps, build row), a
    Separator, the Infra blocks (state branch@sha7 + deploy timestamp
    section with the Deployment button accessory, artifact row), and
    the machine-readable footer as sub-text (frozen format, plain
    `key=value` pairs). Same identity as /status, adapted to the V2
    navigation rules.
    """
    catalog = _load_catalog(config.locale, config.config_dir)
    header = f"# {ANNOUNCEMENT_HEADER} {catalog['title']}"
    if env:
        header = f"{header}\n-# `{env}`"
    container = Container(accent=BLURPLE).add(Text(header))
    release = _release_section(status, catalog)
    if release is not None:
        container = container.add(release)
    container = container.add(*_services_blocks(status, catalog))
    container = container.add(Separator())
    container = container.add(*_infra_blocks(status, catalog))
    container = container.add(Separator())
    container = container.add(Text(f"-# {STATUS_LINK_LABEL} \u00b7 {deploy_footer(status, env=env)}"))
    return UILayout().add(container).build()


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
        "branch_button": "Branch",
        "pull_request_button": "Pull-request",
        "commit_button": "Commit",
        "files_button": "Files",
        "release_button": "Release",
        "version_button": "Version",
        "pipeline_button": "Pipeline",
        "image_button": "Image",
        "deployment_button": "Deployment",
        "built_at_label": "Built",
        "deployed_at_label": "Deployed",
    }
    return {key: str(section.get(key, default)) for key, default in defaults.items()}
