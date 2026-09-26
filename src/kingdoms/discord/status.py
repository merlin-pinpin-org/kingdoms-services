"""The /status command: bot and per-guild operational report.

Generic bot capability (not a mod): it reports uptime, the deployed
artifacts grouped by repository — a Services section (version: PR /
Commit + tree / Release, plus the pinned docker image) and an Infra
section (deploy/<env>@<sha> state and the Deployment #<n> pipeline run)
—, configured games, enabled mods with their declared channels and
roles, and one merged Admins section — bot operators (BOT_ADMINS) and the
invoking guild's admins — as a bullet list of Discord mentions.

Reference: kingdoms-services#35 (bot vs guild admins),
kingdoms-infra#37 (deploy URL plumbing).
"""

from __future__ import annotations

import os
from collections.abc import Iterable

import discord
from discord import app_commands

from kingdoms.core.services.status import StatusService, format_version
from kingdoms.discord.announce import AnnounceConfig, build_announcement_layout
from kingdoms.discord.deploy_render import (
    INFRA_REPO_URL,
    PACKAGE_URL,
    SERVICES_REPO_URL,
    docker_tag,
    relative_time,
    sha7_of,
    short_tag,
)
from kingdoms.discord.ui import Text


def _human_uptime(seconds: float) -> str:
    """Render an uptime duration as a compact human string."""
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


def _guild_admin_ids(guild: discord.Guild, bot_user_id: int | None = None) -> list[int]:
    """Guild admins: members with administrator/manage-guild permission.

    Bots are excluded: the kingdoms bot itself holds manage-guild to operate
    (roles/channels), but it is not an admin a user can contact.
    """
    admins: list[int] = []
    for member in guild.members:
        if member.bot:
            continue
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            if bot_user_id is not None and member.id == bot_user_id:
                continue
            admins.append(member.id)
    return sorted(admins)


def _mention(user_id: str | int) -> str:
    """Render a Discord user mention (clickable profile link)."""
    return f"<@{user_id}>"


def format_admins(
    bot_admins: tuple[str, ...],
    guild: discord.Guild | None,
    bot_user_id: int | None = None,
) -> str:
    """Render one merged Admins section: bot operators + the invoking guild's admins."""
    entries: list[str] = [_mention(uid) for uid in bot_admins]
    if guild is not None:
        entries.extend(_mention(uid) for uid in _guild_admin_ids(guild, bot_user_id))
    if not entries:
        return "*(none — set BOT_ADMINS or grant guild-administrator permissions)*"
    return "\n".join(f"- {entry}" for entry in entries)


def format_deploy(
    deploy_run_url: str,
    deploy_url: str,
    deploy_infra_label: str = "",
    deploy_infra_url: str = "",
    deploy_run_number: str = "",
    deploy_run_ts: str = "",
    infra_commit_ts: str = "",
) -> str:
    """Render the Infra field: state branch, state commit, deploy run.

    Four lines grouped on the kingdoms-infra repository, short labels
    carrying the links: the state branch (`Branch deploy/<env>`), the
    deployed state commit (`Commit <sha7>` linking the commit) with
    its **own** committed date (KINGDOMS_DEPLOY_INFRA_COMMIT_TS —
    distinct from the run date), followed by the Files line (the
    state tree, labeled by the state sha7), and the deployment job
    (`Deployment #<n>`, only the id links, with the run timestamp
    when available). The infra identity is parsed from the
    `deploy/<env>@<sha7>` label; falls back to a single labeled link,
    then to the plain deploy link / n/a when nothing is available.
    """
    lines: list[str] = []
    branch, _, sha = deploy_infra_label.partition("@")
    if branch and deploy_infra_url:
        lines.append(f"Branch [{branch}]({INFRA_REPO_URL}/tree/{branch})")
        if sha:
            commit = f"Commit [{sha}]({INFRA_REPO_URL}/commit/{sha})"
            committed = relative_time(infra_commit_ts)
            if committed:
                commit = f"{commit} {committed}"
            lines.append(commit)
            lines.append(f"Files [{sha}]({deploy_infra_url})")
    elif deploy_infra_label and deploy_infra_url:
        lines.append(f"[{deploy_infra_label}]({deploy_infra_url})")
    if deploy_run_url:
        run_id = f"#{deploy_run_number}" if deploy_run_number else "run"
        run = f"Deployment [{run_id}]({deploy_run_url})"
        if deploy_run_ts.strip().isdigit():
            run = f"{run} <t:{deploy_run_ts.strip()}:R>"
        lines.append(run)
    if lines:
        return "\n".join(lines)
    if deploy_url:
        return f"[deploy]({deploy_url})"
    return "n/a"


def status_uptime(report: dict[str, object]) -> float:
    """Read the uptime field of a status report."""
    return float(report["uptime_seconds"])  # type: ignore[arg-type]


def _files_line(tree_url: str) -> str:
    """Files line: link the tree of the deployed commit, labeled by its sha7.

    The PR pin payload carries the full head sha only inside the tree
    URL (the client_payload is capped at 10 properties): the commit sha
    is its last path segment.
    """
    sha = tree_url.rstrip("/").rsplit("/", 1)[-1]
    if not sha:
        return ""
    return f"Files [{sha[:7]}]({tree_url})"


def format_services_section(
    version: str,
    image: str = "",
    kind: str = "",
    deploy_url: str = "",
    branch: str = "",
    tree_url: str = "",
    ts: str = "",
    commit_ts: str = "",
) -> str:
    """Render the Services repo section, one line per deployed artifact.

    Same identity as the announcement (deploy_render is the shared
    source of truth): the commit line carries **Committed** (the source
    commit date, KINGDOMS_DEPLOY_COMMIT_TS) and the image line carries
    **Built** (the image pin date, deploy_ts) — distinct natures, one
    timestamp each, never the build date on the commit. The docker tag
    renders shortened (sha7 + short stamp) — the full tag never shows.
    Falls back to the untyped single-line render when the pipeline
    provides no typed links.
    """
    lines: list[str] = []
    if branch:
        lines.append(f"Branch [{branch}]({SERVICES_REPO_URL}/tree/{branch})")
    sha = sha7_of(tree_url)
    if kind == "pr" and sha:
        commit = f"Commit [{sha}]({SERVICES_REPO_URL}/commit/{sha})"
        committed = relative_time(commit_ts)
        if committed:
            commit = f"{commit} {committed}"
        lines.append(commit)
        lines.append(_files_line(tree_url))
    lines.append(version)
    if image:
        image_line = f"Image [{short_tag(docker_tag(image))}]({PACKAGE_URL})"
        built = relative_time(ts)
        if built:
            image_line = f"{image_line} {built}"
        lines.append(image_line)
    if not kind and deploy_url and deploy_url not in version:
        lines.append(f"[deploy]({deploy_url})")
    return "\n".join(lines)


def format_latency(latency: float | None) -> str:
    """Render the gateway latency in milliseconds; n/a when unknown."""
    if latency is None or latency < 0:
        return "n/a"
    return f"{round(latency * 1000)} ms"


def build_status_embed(
    status: StatusService,
    guild: discord.Guild | None,
    latency: float | None = None,
    bot_user_id: int | None = None,
) -> discord.Embed:
    """Build the /status embed from the core report + guild context."""
    report = status.report()
    embed = discord.Embed(
        title="Kingdoms — Status",
        color=0x5865F2,
    )
    version = format_version(
        status.deploy_label,
        status.deploy_url,
        kind=status.deploy_kind,
        ref=status.deploy_ref,
        tree_url=status.deploy_tree_url,
        ts=status.deploy_ts,
        pr_title=status.deploy_pr_title,
    )
    embed.add_field(
        name="Services",
        value=format_services_section(
            version,
            status.deploy_image,
            status.deploy_kind,
            status.deploy_url,
            branch=status.deploy_branch,
            tree_url=status.deploy_tree_url,
            ts=status.deploy_ts,
            commit_ts=status.deploy_commit_ts,
        ),
        inline=True,
    )
    embed.add_field(
        name="Infra",
        value=format_deploy(
            status.deploy_run_url,
            status.deploy_url,
            status.deploy_infra_label,
            status.deploy_infra_url,
            status.deploy_run_number,
            status.deploy_run_ts,
            infra_commit_ts=status.deploy_infra_commit_ts,
        ),
        inline=True,
    )
    embed.add_field(
        name="Uptime",
        value=_human_uptime(status_uptime(report)),
        inline=True,
    )
    embed.add_field(name="Latency", value=format_latency(latency), inline=True)

    embed.add_field(
        name="Admins",
        value=format_admins(status.bot_admins, guild, bot_user_id),
        inline=False,
    )

    games = status.games()
    embed.add_field(
        name="Games",
        value=", ".join(games) if games else "*(none configured)*",
        inline=False,
    )

    mods = status.enabled_mods()
    if mods:
        lines = []
        for mod_name, declared in mods.items():
            channels = ", ".join(f"`{mod_name}:{key}`" for key in declared["channels"]) or "—"
            roles = ", ".join(f"`{key}`" for key in declared["roles"]) or "—"
            lines.append(f"**{mod_name}**\nchannels: {channels}\nroles: {roles}")
        embed.add_field(name="Enabled mods", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Enabled mods", value="*(none enabled)*", inline=False)

    return embed


def format_commands(commands: Iterable[object]) -> str:
    """Render the synced Commands section.

    Slash commands grouped by their owning group (the closest equivalent of
    cogs on a bare command tree), then the root-level commands under a
    `core` label. Context menus are not slash commands and are skipped.
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


def register_status_command(
    tree: app_commands.CommandTree[discord.Client],
    status: StatusService,
    sync_target: str = "global",
) -> None:
    """Register the /status slash command on the command tree."""

    @tree.command(name="status", description="Bot status: uptime, mods, games, admins")
    async def status_command(interaction: discord.Interaction) -> None:
        """Answer the /status interaction with the deployment layout.

        One rendering: /status answers with the same Components V2
        layout as the startup announcement (build_announcement_layout)
        — the boot message is a /status posted (non-ephemeral) in the
        guild's bot logs channel. The Commands section (sync scope)
        is the only command-specific extra, appended as a Text block.
        """
        latency: float | None = interaction.client.latency
        if latency != latency or latency == float("inf"):
            latency = None
        config = AnnounceConfig(locale="en")
        sync_scope = sync_target if interaction.guild is not None else "none (DM)"
        commands = Text(
            f"**Commands (sync: {sync_scope})**\n{format_commands(tree.get_commands())}"
        )
        layout = build_announcement_layout(
            status,
            config,
            env=os.environ.get("KINGDOMS_DEPLOY_ENV", ""),
            latency_ms=round(latency * 1000) if latency is not None else None,
            extra_blocks=[commands],
        )
        await interaction.response.send_message(view=layout, ephemeral=True)
