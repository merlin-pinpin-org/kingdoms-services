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

from collections.abc import Iterable

import discord
from discord import app_commands

from kingdoms.core.services.status import StatusService, format_version


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
) -> str:
    """Render the Infra field: state branch, state commit, deploy run.

    Three lines grouped on the kingdoms-infra repository: the state
    branch (`Branch deploy/<env>` linking the branch, + tree), the
    deployed state commit (`@<sha7>` linking the commit, + tree), and
    the deployment job (`Deployment #<n>` with a relative timestamp
    when available). The infra identity is parsed from the
    `deploy/<env>@<sha7>` label; falls back to a single labeled link,
    then to the plain deploy link / n/a when nothing is available.
    """
    lines: list[str] = []
    branch, _, sha = deploy_infra_label.partition("@")
    if branch and deploy_infra_url:
        repo = "https://github.com/merlin-pinpin-org/kingdoms-infra"
        lines.append(f"Branch [{branch}]({repo}/tree/{branch}) ([tree]({deploy_infra_url}))")
        if sha:
            lines.append(f"[@{sha}]({repo}/commit/{sha}) ([tree]({deploy_infra_url}))")
    elif deploy_infra_label and deploy_infra_url:
        lines.append(f"[{deploy_infra_label}]({deploy_infra_url})")
    if deploy_run_url:
        run_text = f"Deployment #{deploy_run_number}" if deploy_run_number else "deploy run"
        run = f"[{run_text}]({deploy_run_url})"
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


def format_services_section(
    version: str,
    image: str = "",
    kind: str = "",
    deploy_url: str = "",
    branch: str = "",
    tree_url: str = "",
    ts: str = "",
) -> str:
    """Render the Services repo section, one line per deployed artifact.

    Groups the deployed-artifact links by repository (the developer's
    layout): Branch <name> (+ tree of the deployed commit), the version
    line (Commit + tree / Release + tree / the triggering Pull-request,
    already rendered by format_version), then the pinned docker image
    (label links to the GHCR package page) with a relative timestamp.
    Falls back to the untyped single-line render when the pipeline
    provides no typed links.
    """
    lines: list[str] = []
    if branch:
        repo = "https://github.com/merlin-pinpin-org/kingdoms-services"
        tree_part = f" ([tree]({tree_url}))" if tree_url else ""
        lines.append(f"Branch [{branch}]({repo}/tree/{branch}){tree_part}")
    lines.append(version)
    if image:
        label = image.rsplit(":", 1)[-1] if ":" in image else image
        package_url = "https://github.com/merlin-pinpin-org/kingdoms-services/pkgs/container/kingdoms-services"
        image_line = f"Image [{label}]({package_url})"
        if ts.strip().isdigit():
            image_line = f"{image_line} <t:{ts.strip()}:R>"
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
        """Answer the /status interaction with the current status embed."""
        latency: float | None = interaction.client.latency
        if latency != latency or latency == float("inf"):
            latency = None
        bot_user_id = interaction.client.user.id if interaction.client.user else None
        embed = build_status_embed(status, interaction.guild, latency, bot_user_id)
        sync_scope = sync_target if interaction.guild is not None else "none (DM)"
        embed.add_field(
            name=f"Commands (sync: {sync_scope})",
            value=format_commands(tree.get_commands()),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
