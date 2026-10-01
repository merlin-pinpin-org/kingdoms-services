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

# The validated salons-first structure (kingdoms#138). Each channel is
# (name, kind): kind is "text", "forum" or "announce" (admin-written,
# everyone-readable). Categories run top-to-bottom in the designer order.
SALONS_FIRST_STRUCTURE: tuple[tuple[str, tuple[tuple[str, str], ...], bool], ...] = (
    ("Profils", (), False),
    (
        "Général",
        (
            ("Présentation", "announce"),
            ("Annonce", "announce"),
            ("Règles", "forum"),
            ("Saison", "text"),
            ("Update", "announce"),
            ("Taverne", "text"),
            ("Suggestion", "forum"),
        ),
        False,
    ),
    ("Conscription", (("Postuler", "text"), ("Candidatures", "text")), False),
    ("Kingdoms", (("Géopolitique", "text"),), False),
    ("Époque", (("Âge sombre", "text"),), False),
    ("Royaume Gaïa", (("Patrouille", "text"), ("Territoire", "text"), ("Exploration", "text")), False),
    ("Champs de Bataille", (("Délais-attaque", "text"), ("Attaquer", "text"), ("Pourparlers", "text")), False),
    ("Scriptorium", (("Seigneurs", "text"), ("Diplomatie", "text"), ("Cadastre", "text")), False),
    ("Admin", (("Paramètres", "text"), ("Demandes", "text")), True),
    ("Support", (("Question", "forum"), ("Signaler un Bug", "forum")), False),
)

ADMIN_ONLY_CHANNELS = {"Candidatures"}
PROFILES_CATEGORY = "Profils"


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


def _slug(name: str) -> str:
    """Normalize a channel/category name the way Discord does.

    Discord lowercases, strips accents and turns spaces into dashes:
    "Âge sombre" is stored as "age-sombre". Comparing raw names made
    the bootstrap re-create the Époque channel on every run.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", name.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.replace(" ", "-").strip("-")


def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    wanted = _slug(name)
    for category in getattr(guild, "categories", []):
        if _slug(category.name) == wanted:
            return category  # type: ignore[no-any-return]
    return None


def _find_channel(
    guild: discord.Guild, category: discord.CategoryChannel | None, name: str
) -> discord.abc.GuildChannel | None:
    """Find a structure channel by name, whatever its kind (text or forum)."""
    wanted = _slug(name)
    candidates = [*guild.text_channels, *getattr(guild, "forums", [])]
    for channel in candidates:
        if _slug(channel.name) == wanted and (category is None or channel.category_id == category.id):
            return channel  # type: ignore[no-any-return]
    return None


def _announce_overwrites(
    guild: discord.Guild,
) -> dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite]:
    """Admin-written, everyone-readable: @everyone may not send messages."""
    overwrites: dict[discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
    }
    if guild.me is not None:
        overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return overwrites


async def provision_structure(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Create (or adopt) the salons-first structure on a guild.

    Returns (created, adopted) display paths ("Category/Channel"). A
    channel that already exists by name is adopted, never duplicated —
    re-running the bootstrap is safe.
    """
    created: list[str] = []
    adopted: list[str] = []

    for index, (category_name, channel_names, category_admin_only) in enumerate(SALONS_FIRST_STRUCTURE):
        category = _find_category(guild, category_name)
        if category is None:
            overwrites = _everyone_overwrites(guild) if category_admin_only else {}
            category = await guild.create_category(
                category_name,
                reason="kingdoms: salons-first bootstrap",
                overwrites=overwrites,
                position=index,
            )
            created.append(category_name)
        else:
            adopted.append(category_name)

        for channel_name, channel_kind in channel_names:
            existing = _find_channel(guild, category, channel_name)
            if existing is not None:
                adopted.append(f"{category_name}/{channel_name}")
                continue
            admin_only = category_admin_only or channel_name in ADMIN_ONLY_CHANNELS
            if admin_only:
                overwrites = _everyone_overwrites(guild)
            elif channel_kind == "announce":
                overwrites = _announce_overwrites(guild)
            else:
                overwrites = {}
            if channel_kind == "forum":
                await guild.create_forum(
                    channel_name,
                    reason=f"kingdoms: provision the {channel_name} forum",
                    category=category,
                    overwrites=overwrites,
                )
            else:
                await guild.create_text_channel(
                    channel_name,
                    reason=f"kingdoms: provision the {channel_name} channel",
                    category=category,
                    overwrites=overwrites,
                )
            created.append(f"{category_name}/{channel_name}")

        await _reorder_category(category, channel_names)

    return created, adopted


async def _reorder_category(
    category: discord.CategoryChannel,
    channel_names: tuple[tuple[str, str], ...],
) -> None:
    """Impose the designer's channel order inside one category (and adopt renames)."""
    for position, (channel_name, _kind) in enumerate(channel_names):
        channel = _find_channel(category.guild, category, channel_name)
        if channel is None or not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            continue
        current = _slug(getattr(channel, "name", ""))
        if current != _slug(channel_name):
            legacy = {"saison": {"parametre-saison-ii", "parameter-season-ii"}}
            if current in legacy.get(_slug(channel_name), set()):
                try:
                    await channel.edit(name=channel_name, reason="kingdoms: rename legacy channel")
                except Exception:
                    logger.warning("KINGDOM SETUP: channel rename failed", exc_info=True)
        try:
            await channel.edit(position=position, reason="kingdoms: enforce channel order")
        except Exception:
            logger.warning("KINGDOM SETUP: channel reorder failed", exc_info=True)


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
