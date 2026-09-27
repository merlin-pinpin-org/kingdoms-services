"""Deployment announcement: the "start" lifecycle event (#52, #109).

On real gateway connection the bot posts the deployment announcement
in each guild's ``🤖-bot-logs`` channel — resolved and provisioned by
the core :class:`~kingdoms.core.services.logs.LogService` (cache-aside:
Redis → MongoDB → adoption → creation, admin-only by default).

One deploy identity, one rendering: the announcement is a Components
V2 layout built through the UI SDK (:mod:`kingdoms.discord.ui`).
Per the SDK navigation rules, text blocks carry plain labels only
(links and line breaks do not render in V2 text) — every artifact is
a link button with a generic label (Branch, Commit, Tag, Files, Job,
Image, Deploy); the identity itself (branch, tag, sha7, CI job id,
docker tag + full digest, deploy job id) rides in the text lines
above each button row, each job id line sitting under a Separator.

Sections: Bot (uptime, admins, games, mods, gateway latency, synced
commands), Services (source identity + commit date, CI job date,
build artifacts), Infra (state identity + commit date, deploy
job + run date) — each artifact's timestamp sits directly under it,
under a row of generically labeled buttons carrying the links. The deployed
environment rides as a badge under the title; there is no machine
footer — the
kingdoms-infra post-deploy battery (kingdoms-infra#78) reads the
pinned state from the state file, not from the message.

Announcements can be silenced entirely (``KINGDOMS_ANNOUNCE_ENABLED=0``)
— the CI/CD smoke bot uses this to boot against the real gateway
without posting startup messages in the shared guilds.

Without a LogService (local runs, unit tests), the announcement
degrades to nothing — silently skipped. Delivery is best-effort either
way: startup readiness never depends on message delivery.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import discord

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.status import StatusService
from kingdoms.discord.deploy_render import (
    INFRA_REPO_URL,
    PACKAGE_URL,
    SERVICES_REPO_URL,
    docker_tag,
    full_digest,
    is_release_kind,
    relative_time,
    sha7_of,
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

logger = logging.getLogger("kingdoms.bot.announce")

_VERSION_BUTTON_KINDS = {
    "pr": "pull_request_button",
    "main": "commit_button",
    "release": "release_button",
    "prerelease": "release_button",
}


ANNOUNCEMENT_HEADER = "🚀"


@dataclass(frozen=True, slots=True)
class AnnounceConfig:
    """Announcement configuration (locale only — channels are owned by LogService)."""

    locale: str = "en"
    config_dir: Path = Path("config")


def render_commands(commands: Iterable[object]) -> str:
    """Render the synced Commands block (one rendering, everywhere).

    Slash commands grouped by their owning group (the closest
    equivalent of cogs on a bare command tree), then the root-level
    commands under a `core` label. Context menus are not slash
    commands and are skipped.
    """
    groups: dict[str, list[str]] = {}
    for cmd in commands:
        if not isinstance(getattr(cmd, "description", None), str):
            continue
        name = getattr(cmd, "name", "")
        parent = getattr(cmd, "root_parent", None)
        owner = getattr(parent, "name", None) or "core"
        groups.setdefault(owner, []).append(name)
    if not groups:
        return "*(none)*"
    lines = []
    for owner in sorted(groups, key=lambda k: (k == "core", k)):
        names = sorted(groups[owner])
        lines.append(f"**{owner}**: " + (", ".join(f"/{n}" for n in names) or "—"))
    return "\n".join(lines)


def commands_section(sync_scope: str, commands: Iterable[object], locale: str, config_dir: Path) -> str:
    """Render the synced Commands section (one rendering, boot and /status).

    The header carries the sync scope through the locale catalog
    (announce.commands_label / announce.sync_none_label).
    """
    catalog = _load_catalog(locale, config_dir)
    scope = sync_scope if sync_scope else catalog.get("sync_none_label", "none (DM)")
    header = catalog.get("commands_label", "Commands (sync: {scope})").format(scope=scope)
    rendered = render_commands(commands) if commands is not None else ""
    return f"**{header}**\n{rendered}" if rendered else ""


def _commands_blocks(commands: str, command_ids: Iterable[object]) -> list[object]:
    """Assemble the Commands block (text + native mentions).

    The synced command ids are inlined as native mentions (</name:id>)
    — clickable in Discord; before the first sync the text stands.
    """
    if not commands:
        return []
    mentions = command_mentions(command_ids)
    if not mentions:
        return [Text(commands)]
    return [Text(f"{commands}\n{' '.join(mentions)}")]


def command_mentions(commands: Iterable[object]) -> list[str]:
    """Render the native command mentions (</name:id>) when synced.

    A synced command mention is clickable in Discord — it opens the
    command picker. Before the first sync the ids are absent and the
    plain /name rendering stands.
    """
    mentions: list[str] = []
    for cmd in commands:
        command_id = getattr(cmd, "id", None)
        name = getattr(cmd, "name", "")
        if isinstance(command_id, int) and name:
            mentions.append(f"</{name}:{command_id}>")
    return mentions


def _release_section(status: StatusService, catalog: dict[str, str]) -> Section | Text | None:
    """Build the headline: what is deployed (PR title or version) + its button.

    Releases headline as ``Version <ref>`` (the vX.Y.Z tag), pre-releases
    as ``Pre-release <ref>`` (the -rc<n> classifier rides in the ref) —
    a final release is celebrated (tada); PRs keep their title. A Section
    with the PR/Commit/Release button as accessory when the pipeline
    provides a URL; a plain Text fallback (title only); None when there
    is nothing to headline.
    """
    title = status.deploy_pr_title or ""
    if is_release_kind(status.deploy_kind) and status.deploy_ref:
        label = catalog["prerelease_label"] if status.deploy_kind == "prerelease" else catalog["version_label"]
        emoji = "" if status.deploy_kind == "prerelease" else " 🎉 "
        title = f"{emoji}{label} {status.deploy_ref}".strip()
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


def _deploy_commit_sha(status: StatusService) -> str:
    """Resolve the sha7 of the deployed commit.

    Releases pin the tag, not the sha: the tag rides as the last path
    segment of the tree URL, so it must never be rendered as a commit —
    the resolved sha (KINGDOMS_DEPLOY_COMMIT, written by the pin from
    the GitHub API) stands in for the sha the emitters cannot pass
    (the client_payload is capped at 10 properties).
    """
    if is_release_kind(status.deploy_kind):
        return status.deploy_commit[:7] if status.deploy_commit else ""
    return sha7_of(status.deploy_tree_url)


def _services_identity_text(status: StatusService, catalog: dict[str, str]) -> Text | None:
    """Build the source identity lines: branch, tag (releases), commit.

    A release deploy carries both the tag (vX.Y.Z) and the commit it
    points at — distinct lines, never the tag rendered as a commit.
    """
    sha = _deploy_commit_sha(status)
    lines = [
        line
        for line in (
            _line(f"🌿 {catalog['branch_label']}", status.deploy_branch),
            _line(f"🏷️ {catalog['tag_label']}", status.deploy_ref)
            if is_release_kind(status.deploy_kind) and status.deploy_ref
            else "",
            _line(f"🔧 {catalog['commit_label']}", sha, relative_time(status.deploy_commit_ts)),
        )
        if line
    ]
    return Text("\n".join(lines)) if lines else None


def _services_artifact_row(status: StatusService, catalog: dict[str, str]) -> list[Button]:
    """Build the Services artifact buttons: branch, commit, files, version."""
    buttons: list[Button] = []
    if status.deploy_branch:
        buttons.append(Button(f"🌿 {catalog['branch_label']}", f"{SERVICES_REPO_URL}/tree/{status.deploy_branch}"))
    sha = _deploy_commit_sha(status)
    if sha:
        buttons.append(Button(f"🔧 {catalog['commit_label']}", f"{SERVICES_REPO_URL}/commit/{sha}"))
        if status.deploy_tree_url:
            buttons.append(Button(f"🗂️ {catalog['files_label']}", status.deploy_tree_url))
    if is_release_kind(status.deploy_kind) and status.deploy_ref:
        buttons.append(Button(f"🏷️ {catalog['tag_label']}", f"{SERVICES_REPO_URL}/releases/tag/{status.deploy_ref}"))
    if status.deploy_url and not _release_section(status, catalog):
        buttons.append(Button(f"🔗 {catalog['link_label']}", status.deploy_url))
    return buttons


def _services_build_row(status: StatusService, catalog: dict[str, str]) -> list[Button]:
    """Build the build buttons: CI job + package image.

    Every button carries a generic label: the CI job by ``Job`` (its
    id rides in the Job CI line above the image), the image by its
    shortened docker tag — never an anonymous emoji.
    """
    buttons: list[Button] = []
    ci_url = f"{SERVICES_REPO_URL}/actions/runs/{status.deploy_ci_run_id}" if status.deploy_ci_run_id else ""
    if ci_url:
        buttons.append(Button(f"⚙️ {catalog['ci_label']}", ci_url))
    if status.deploy_image:
        buttons.append(Button(f"📦 {catalog['image_label']}", PACKAGE_URL))
    return buttons


def _services_blocks(status: StatusService, catalog: dict[str, str]) -> list[object]:
    """Build the Services blocks: identity + jobs + buttons.

    The source identity (branch, tag, commit) and the CI job line
    carry their own timestamp on the line directly under them — the
    build date rides on the Job CI line, the image line carries no
    date (it is the job's artifact, not its own moment). The CI job
    line sits under a Separator, right above the image line it built.
    """
    blocks: list[object] = [Text(f"**{catalog['services_label']}**")]
    identity = _services_identity_text(status, catalog)
    if identity is not None:
        blocks.append(identity)
    first = _services_artifact_row(status, catalog)
    if first:
        blocks.append(Row(*first))
    ci_number = status.deploy_ci_run_number or status.deploy_ci_run_id
    if ci_number:
        blocks.append(Separator())
        blocks.append(
            Text(
                _line(
                    f"⚙️ {catalog['job_ci_label']}",
                    f"#{ci_number}",
                    relative_time(status.deploy_ci_run_ts),
                )
            )
        )
    image = status.deploy_image or status.deploy_label
    tag = docker_tag(image)
    if tag:
        digest = full_digest(image)
        lines = [_line(f"📦 {catalog['image_label']}", tag)]
        if digest:
            lines.append(f"`{digest}`")
        blocks.append(Text("\n".join(lines)))
    build = _services_build_row(status, catalog)
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
        row.append(Button(f"🌿 {catalog['branch_label']}", f"{INFRA_REPO_URL}/tree/{branch}"))
    if sha:
        row.append(Button(f"🔧 {catalog['commit_label']}", f"{INFRA_REPO_URL}/commit/{sha}"))
    if status.deploy_infra_url:
        row.append(Button(f"🗂️ {catalog['files_label']}", status.deploy_infra_url))
    if row:
        blocks.append(Row(*row))
    run_ts = relative_time(status.deploy_run_ts)
    if status.deploy_run_url:
        run_id = status.deploy_run_number or status.deploy_run_url.rsplit("/", 1)[-1]
        blocks.append(Separator())
        blocks.append(Text(_line(f"🚀 {catalog['job_deploy_label']}", f"#{run_id}", run_ts)))
        blocks.append(
            Row(
                Button(f"🚀 {catalog['deploy_label']}", status.deploy_run_url),
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
    lines: list[str] = [f"- {catalog['uptime_label']}: {relative_uptime(status.uptime_seconds())}"]
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


def relative_uptime(seconds: float) -> str:
    """Render the boot moment as a Discord relative timestamp (/status parity)."""
    boot_unix = int(time.time() - max(0.0, seconds))
    return f"<t:{boot_unix}:R>"


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
    commands: str = "",
    command_ids: Iterable[object] = (),
) -> discord.ui.LayoutView:
    """Build the Components V2 announcement: headline + sections + footer.

    Layout: one accent Container holding a header TextDisplay (title +
    env badge), the headline section (the deployed PR title or version
    with its button accessory), the Bot blocks — the operational
    identity, including the Commands block when provided —, a
    Separator, the Services blocks, a Separator, and the Infra blocks.
    Same rendering for the startup announcement and /status: the boot
    message is a non-ephemeral /status posted in the guild's bot logs
    channel, so the Commands block rides in both.
    """
    catalog = _load_catalog(config.locale, config.config_dir)
    commands_block = _commands_blocks(commands, command_ids)
    header = f"# {ANNOUNCEMENT_HEADER} {catalog['title']}"
    if env:
        header = f"{header}\n-# `{env}`"
    container = Container(accent=BLURPLE).add(Text(header))
    release = _release_section(status, catalog)
    if release is not None:
        container = container.add(release)
    container = container.add(*_bot_blocks(status, catalog, latency_ms))
    if commands_block is not None:
        container = container.add(*commands_block)
    container = container.add(Separator())
    container = container.add(*_services_blocks(status, catalog))
    container = container.add(Separator())
    container = container.add(*_infra_blocks(status, catalog))
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
    commands: Iterable[object] | None = None,
    sync_scope: str = "",
) -> None:
    """Post the deployment announcement per guild in its bot logs channel.

    ``locale_resolver`` (an async ``guild_id -> locale`` callable, the
    LogService's ``get_locale``) localizes per guild when provided —
    each guild's language choice (managed through /admin) applies to
    its own announcement; without it the AnnounceConfig locale stands.
    ``commands`` (the synced command tree) rides the Bot section as
    the Commands block — the boot message is a non-ephemeral /status.
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
        commands_block = (
            commands_section(sync_scope, commands, locale, config.config_dir) if commands is not None else ""
        )
        layout = build_announcement_layout(
            status,
            guild_config,
            env=deploy_env,
            thumbnail_url=thumbnail_url,
            latency_ms=latency_ms,
            commands=commands_block,
            command_ids=commands or (),
        )
        event = LifecycleEvent(kind="start", message="", layout=layout)
        await logs_service.log_event(str(guild.id), event, pin=True)
        logger.info("STARTUP ANNOUNCEMENT SENT to guild %s (locale=%s, pinned)", guild.id, locale)


def _load_catalog(locale: str, config_dir: Path) -> dict[str, str]:
    """Resolve the announce strings through the shared MessageCatalog.

    One yaml loader for every localized surface (announce, admin): the
    catalog flattens the nested sections, falls back to English and
    never raises — an unknown key renders as its dotted name.
    """
    return MessageCatalog(config_dir).section("announce", locale)
