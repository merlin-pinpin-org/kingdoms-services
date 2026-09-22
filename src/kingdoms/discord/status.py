"""The /status command: bot and per-guild operational report.

Generic bot capability (not a mod): it reports uptime, version, configured
games, enabled mods with their declared channels and roles, bot admins
(BOT_ADMINS) and the invoking guild's admins.

Reference: kingdoms-services#35 (bot vs guild admins).
"""

from __future__ import annotations

import discord
from discord import app_commands

from kingdoms.core.services.status import StatusService


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


def _guild_admin_names(guild: discord.Guild) -> list[str]:
    """Guild admins: members with administrator/manage-guild permission."""
    admins: list[str] = []
    for member in guild.members:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            admins.append(f"{member.display_name} ({member.id})")
    return sorted(admins)


def status_version(report: dict[str, object]) -> str:
    """Read the version field of a status report."""
    return str(report["version"])


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
    embed.add_field(name="Version", value=status_version(report), inline=True)
    embed.add_field(
        name="Uptime",
        value=_human_uptime(status_uptime(report)),
        inline=True,
    )
    embed.add_field(name="Latency", value=format_latency(latency), inline=True)

    bot_admins = status.bot_admins
    embed.add_field(
        name="Bot admins",
        value=", ".join(bot_admins) if bot_admins else "*(none configured — set BOT_ADMINS)*",
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

    if guild is not None:
        admins = _guild_admin_names(guild)
        embed.add_field(
            name=f"Guild admins — {guild.name}",
            value="\n".join(admins) if admins else "*(none found)*",
            inline=False,
        )

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
