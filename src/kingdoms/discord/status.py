"""The /status command: bot and per-guild operational report.

Generic bot capability (not a mod): it reports uptime, the deployed version
as a labeled GitHub link (KINGDOMS_DEPLOY_LABEL + KINGDOMS_DEPLOY_URL),
configured games, enabled mods with their declared channels and roles, and
one merged Admins section — bot operators (BOT_ADMINS) and the invoking
guild's admins — as a bullet list of Discord mentions.

Reference: kingdoms-services#35 (bot vs guild admins),
kingdoms-infra#37 (deploy URL plumbing).
"""

from __future__ import annotations

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


def _guild_admin_ids(guild: discord.Guild) -> list[int]:
    """Guild admins: members with administrator/manage-guild permission."""
    admins: list[int] = []
    for member in guild.members:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            admins.append(member.id)
    return sorted(admins)


def _mention(user_id: str | int) -> str:
    """Render a Discord user mention (clickable profile link)."""
    return f"<@{user_id}>"


def format_admins(
    bot_admins: tuple[str, ...],
    guild: discord.Guild | None,
) -> str:
    """Render one merged Admins section: bot operators + the invoking guild's admins."""
    entries: list[str] = [_mention(uid) for uid in bot_admins]
    if guild is not None:
        entries.extend(_mention(uid) for uid in _guild_admin_ids(guild))
    if not entries:
        return "*(none — set BOT_ADMINS or grant guild-administrator permissions)*"
    return "\n".join(f"- {entry}" for entry in entries)


def format_deploy_url(url: str) -> str:
    """Render the deployed-artifact link; n/a when the pipeline provided none."""
    return url if url else "n/a"


def status_uptime(report: dict[str, object]) -> float:
    """Read the uptime field of a status report."""
    return float(report["uptime_seconds"])  # type: ignore[arg-type]


def format_latency(latency: float | None) -> str:
    """Render the gateway latency in milliseconds; n/a when unknown."""
    if latency is None or latency < 0:
        return "n/a"
    return f"{round(latency * 1000)} ms"


def build_status_embed(
    status: StatusService,
    guild: discord.Guild | None,
    latency: float | None = None,
) -> discord.Embed:
    """Build the /status embed from the core report + guild context."""
    report = status.report()
    embed = discord.Embed(
        title="Kingdoms — Status",
        color=0x5865F2,
    )
    embed.add_field(name="Version", value=format_version(status.deploy_label, status.deploy_url), inline=True)
    embed.add_field(
        name="Uptime",
        value=_human_uptime(status_uptime(report)),
        inline=True,
    )
    embed.add_field(name="Latency", value=format_latency(latency), inline=True)
    embed.add_field(name="Deploy", value=format_deploy_url(status.deploy_url), inline=True)

    embed.add_field(
        name="Admins",
        value=format_admins(status.bot_admins, guild),
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


def register_status_command(
    tree: app_commands.CommandTree[discord.Client],
    status: StatusService,
) -> None:
    """Register the /status slash command on the command tree."""

    @tree.command(name="status", description="Bot status: uptime, mods, games, admins")
    async def status_command(interaction: discord.Interaction) -> None:
        """Answer the /status interaction with the current status embed."""
        latency: float | None = interaction.client.latency
        if latency != latency or latency == float("inf"):
            latency = None
        embed = build_status_embed(status, interaction.guild, latency)
        await interaction.response.send_message(embed=embed, ephemeral=True)
