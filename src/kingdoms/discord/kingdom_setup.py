"""The /kingdom bootstrap command (kingdoms#138): salons-first setup.

kingdoms-services#175 — the structure is data: the channel groups
(categories), channel kinds (text/forum/announce) and admin-only flags
live in ``config/mods/kingdoms.yaml`` and the core ``ChannelService``
provisions them (cache -> database -> adoption -> creation, idempotent,
adopt-by-slug). This module only drives the core and renders the report:
no feature code creates Discord channels, categories or roles directly.

Name helpers (``_slug``, ``_find_category``) stay lookup-only: finding an
existing channel by its Discord slug is reading the guild, not
provisioning it.
"""

from __future__ import annotations

import logging
import unicodedata

import discord
from discord import app_commands

from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import BLURPLE, Container, Separator, Text, UILayout

logger = logging.getLogger("kingdoms.kingdom")

MOD_NAME = "kingdoms"


def _slug(name: str) -> str:
    """Normalize a channel/category name the way Discord does.

    Discord lowercases, strips accents and turns spaces into dashes:
    "Âge sombre" is stored as "age-sombre".
    """
    decomposed = unicodedata.normalize("NFKD", name.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.replace(" ", "-").strip("-")


def _find_category(guild: discord.Guild, name: str) -> discord.CategoryChannel | None:
    """Find a category by its Discord slug (read-only lookup)."""
    wanted = _slug(name)
    for category in getattr(guild, "categories", []):
        if _slug(getattr(category, "name", "")) == wanted:
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



EPOCH_CATEGORY = "Époque"
"""The group that holds the renameable current-age channel (kingdoms#138):
the channel is renamed at each age switch, so the core adopts it by group
(``adopt: group_single`` in the declaration), whatever its name."""

EPOCH_CHANNEL_KEY = "epoch"


async def _enforce_epoch_overwrites(guild: discord.Guild, channel: discord.TextChannel) -> None:
    """Make the epoch channel read-only for @everyone (best effort).

    Decision (lecture stricte): the bot writes the current age, the
    players read it. Enforcement is idempotent and re-applied at every
    bootstrap so adopted channels converge too.
    """
    try:
        overwrite = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
        if guild.me is not None:
            await channel.set_permissions(
                guild.me, overwrite=discord.PermissionOverwrite(view_channel=True, send_messages=True)
            )
    except Exception:
        logger.warning("KINGDOM SETUP: epoch overwrites failed", exc_info=True)

async def provision_structure(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Provision the declared salons-first structure through the core.

    The declaration (``config/mods/kingdoms.yaml``) is the single source
    of truth: groups in declaration order, then each group's channels —
    kinds, admin-only flags and positions are data. Existing channels are
    adopted (never duplicated); re-running is safe. Returns (created,
    adopted) display paths.
    """
    from kingdoms.discord.kingdom_persistent import _wiring

    wiring = _wiring()
    channel_service = wiring.channel_service
    if channel_service is None:
        raise RuntimeError(
            "the ChannelService is not wired — the salons-first bootstrap needs the platform services"
        )
    report = await channel_service.provision_mod_channels(str(guild.id), MOD_NAME)
    epoch_id = report.channel_ids.get(EPOCH_CHANNEL_KEY)
    epoch_channel = guild.get_channel(int(epoch_id)) if epoch_id and epoch_id.isdigit() else None
    if isinstance(epoch_channel, discord.TextChannel):
        await _enforce_epoch_overwrites(guild, epoch_channel)
    return list(report.created), list(report.adopted)


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
