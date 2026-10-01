"""The /kingdom bootstrap command (kingdoms#138): salons-first setup.

Design (validated with the game designer, kingdoms repo issue #138):

- **one command for the whole lifecycle** — ``/kingdom`` (admin only)
  bootstraps the mod: it creates the Discord categories and channels of
  the salons-first architecture and re-runs are idempotent (existing
  channels are adopted, never duplicated);
- **no "kingdoms-" prefix in names** — a root category ``Kingdoms``,
  simple channel names, per the designer's decision;
- **permissions** — the Admin category and the Candidatures channel are
  admin-only (@everyone denied view); kingdom categories are
  per-kingdom and provisioned later, at kingdom validation, by the
  enrollment flow (not by this bootstrap); the Profils category holds
  one private channel per player, provisioned at application time.

The command validates access at invocation time (BOT_ADMINS or guild
administrators) and answers with a report of what was created/adopted.
Panels themselves (Postuler, Admin settings, …) are deployed by later
slices on top of this structure.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands

from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import BLURPLE, Container, Separator, Text, UILayout

logger = logging.getLogger("kingdoms.kingdom")

# (category_name, [channel names], admin_only) — the validated
# salons-first structure, in the designer's order (kingdoms#138).
SALONS_FIRST_STRUCTURE: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("Conscription", ("Postuler", "Candidatures"), False),
    ("Kingdoms", ("Géopolitique",), False),
    ("Époque", ("Âge sombre",), False),
    ("Royaume Gaïa", ("Patrouille", "Territoire", "Exploration"), False),
    ("Champs de Bataille", ("Délais-attaque", "Attaquer", "Pourparlers"), False),
    ("Scriptorium", ("Seigneurs", "Diplomatie", "Cadastre"), False),
    ("Profils", (), False),
    ("Admin", ("Paramètres", "Demandes"), True),
)

ADMIN_ONLY_CHANNELS = {"Candidatures"}


def _everyone_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    """Build the admin-only overwrites: @everyone denied, the bot allowed."""
    overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    for category in getattr(guild, "categories", []):
        if category.name.lower() == name.lower():
            return category  # type: ignore[no-any-return]
    return None


def _find_text_channel(
    guild: discord.Guild, category: discord.CategoryChannel | None, name: str
) -> discord.TextChannel | None:
    for channel in guild.text_channels:
        if channel.name.lower() == name.lower() and (category is None or channel.category_id == category.id):
            return channel
    return None


async def provision_structure(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Create (or adopt) the salons-first structure on a guild.

    Returns (created, adopted) display paths ("Category/Channel"). A
    channel that already exists by name is adopted, never duplicated —
    re-running the bootstrap is safe.
    """
    created: list[str] = []
    adopted: list[str] = []

    for category_name, channel_names, category_admin_only in SALONS_FIRST_STRUCTURE:
        category = _find_category(guild, category_name)
        if category is None:
            overwrites = _everyone_overwrites(guild) if category_admin_only else {}
            category = await guild.create_category(
                category_name,
                reason="kingdoms: salons-first bootstrap",
                overwrites=overwrites,
            )
            created.append(category_name)
        else:
            adopted.append(category_name)

        for channel_name in channel_names:
            existing = _find_text_channel(guild, category, channel_name)
            if existing is not None:
                adopted.append(f"{category_name}/{channel_name}")
                continue
            admin_only = category_admin_only or channel_name in ADMIN_ONLY_CHANNELS
            overwrites = _everyone_overwrites(guild) if admin_only else {}
            await guild.create_text_channel(
                channel_name,
                reason=f"kingdoms: provision the {channel_name} channel",
                category=category,
                overwrites=overwrites,
            )
            created.append(f"{category_name}/{channel_name}")

    return created, adopted


def build_setup_report_view(
    created: list[str],
    adopted: list[str],
    locale: str = "en",
) -> discord.ui.LayoutView:
    """Build the bootstrap report layout (what was created vs adopted)."""
    fr = locale.startswith("fr")
    title = "⚒️ Kingdoms — installation" if fr else "⚒️ Kingdoms — setup"
    created_label = "Créés" if fr else "Created"
    adopted_label = "Récupérés" if fr else "Adopted"
    container = Container(accent=BLURPLE).add(Text(f"# {title}"))
    if created:
        container.add(Separator()).add(Text(f"**{created_label}** : " + ", ".join(created)))
    if adopted:
        container.add(Separator()).add(Text(f"**{adopted_label}** : " + ", ".join(adopted)))
    if not created and not adopted:
        container.add(Separator()).add(Text("✅ Structure already in place."))
    return UILayout().add(container).build()


def _is_allowed(interaction: discord.Interaction, bot_admins: tuple[str, ...]) -> bool:
    """Whether the invoking member may bootstrap (BOT_ADMINS or admin)."""
    user_id = getattr(interaction.user, "id", None)
    if user_id is not None and str(user_id) in bot_admins:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.administrator)


def register_kingdom_command(
    tree: app_commands.CommandTree[discord.Client],
    bot_admins: tuple[str, ...] = (),
) -> None:
    """Register the /kingdom bootstrap command (admin only) on the tree."""

    @tree.command(
        name=localized("commands.kingdom_name", "kingdom"),
        description=localized(
            "commands.kingdom_description",
            "Bootstrap the Kingdoms salons (admin only)",
        ),
    )
    @app_commands.default_permissions(administrator=True)
    async def kingdom_command(interaction: discord.Interaction) -> None:
        """Provision the salons-first structure and answer with a report."""
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("The /kingdom command must run inside a server.", ephemeral=True)
            return
        if not _is_allowed(interaction, bot_admins):
            await interaction.response.send_message(
                "You are not allowed to bootstrap Kingdoms (admins only).",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            created, adopted = await provision_structure(guild)
        except Exception as exc:
            logger.exception("KINGDOM SETUP: provisioning failed for guild %s", guild.id)
            await interaction.followup.send(f"❌ Setup failed: `{type(exc).__name__}: {exc}`"[:2000], ephemeral=True)
            return
        locale = str(interaction.locale) if interaction.locale else "en"
        view = build_setup_report_view(created, adopted, locale)
        await interaction.followup.send(view=view, ephemeral=True)
