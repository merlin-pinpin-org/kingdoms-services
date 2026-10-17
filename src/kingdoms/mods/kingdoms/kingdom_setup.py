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
from typing import cast

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
    kinds, admin-only flags and positions are data. Existing channels
    are adopted (never duplicated); re-running is safe. Returns the
    (created, adopted) display paths from the provision report.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    channel_service = wiring.channel_service
    if channel_service is None:
        raise RuntimeError("the ChannelService is not wired — the salons-first bootstrap needs the platform services")
    report = await channel_service.provision_mod_channels(str(guild.id), MOD_NAME)
    epoch_id = report.channel_ids.get(EPOCH_CHANNEL_KEY)
    epoch_channel = guild.get_channel(int(epoch_id)) if epoch_id and epoch_id.isdigit() else None
    if isinstance(epoch_channel, discord.TextChannel):
        await _enforce_epoch_overwrites(guild, epoch_channel)
    # admin-console-v1: the console salon gets its pinned welcome message.
    # admin-console-v2 (Drasah 17/10): plus the interactive panel — the
    # buttons and the ⌨️ Commande… modal answer WITHOUT the message
    # content intent (the deaf-listener workaround of the v2 spec).
    console_id = report.channel_ids.get("console")
    console_channel = guild.get_channel(int(console_id)) if console_id and console_id.isdigit() else None
    if isinstance(console_channel, discord.TextChannel):
        from kingdoms.mods.kingdoms.admin_console import ensure_console_panel, ensure_console_welcome

        await ensure_console_welcome(console_channel)
        await ensure_console_panel(console_channel)
    await cleanup_legacy_structure(guild, str(epoch_id or ""))
    return list(report.created), list(report.adopted)


LEGACY_SALON_SLUGS: tuple[str, ...] = ("seigneurs", "diplomatie", "cadastre")
"""The retired salons-first rubric salons (patch restructuration-royaume):
their content moved into the Scriptorium ephemeral rubrics."""

LEGACY_CATEGORY_NAMES: tuple[str, ...] = ("Époque", "Scriptorium")
"""The retired group categories — the epoch salon now lives under Kingdoms."""

KINGDOMS_CATEGORY_ORDER: tuple[str, ...] = (
    "📜 Scriptorium",
    "Marché",
    "Géopolitique",
    "🏺 Âge",
)
"""Drasah's chosen order of the four Kingdoms-category salons."""


async def _cleanup_legacy_salons(guild: discord.Guild, epoch_channel_id: int) -> list[str]:
    """Delete the retired rubric salons (Seigneurs/Diplomatie/Cadastre)."""
    removed: list[str] = []
    for slug in LEGACY_SALON_SLUGS:
        for channel in [c for c in guild.text_channels if _slug(c.name) == slug]:
            if channel.id == epoch_channel_id:
                continue
            try:
                await channel.delete()
                removed.append(str(channel.name))
            except Exception:
                logger.warning("KINGDOM SETUP: legacy salon removal failed", exc_info=True)
    return removed


async def _cleanup_legacy_categories(guild: discord.Guild, epoch_channel_id: int) -> list[str]:
    """Delete the retired categories (children first, epoch spared)."""
    removed: list[str] = []
    for name in LEGACY_CATEGORY_NAMES:
        category = _find_category(guild, name)
        if category is None:
            continue
        for channel in list(getattr(category, "channels", []) or []):
            if getattr(channel, "id", 0) == epoch_channel_id:
                continue
            try:
                await channel.delete()
                removed.append(str(channel.name))
            except Exception:
                logger.warning("KINGDOM SETUP: legacy category child removal failed", exc_info=True)
        try:
            await category.delete()
            removed.append(str(category.name))
        except Exception:
            logger.warning("KINGDOM SETUP: legacy category removal failed", exc_info=True)
    return removed


async def _reorder_kingdoms_salons(guild: discord.Guild) -> None:
    """Force Drasah's order of the four Kingdoms-category salons."""
    category = _find_category(guild, "Kingdoms")
    if category is None:
        return
    for position, name in enumerate(KINGDOMS_CATEGORY_ORDER):
        channel = _find_channel(guild, category, name)
        if channel is None:
            continue
        try:
            await cast("discord.TextChannel", channel).edit(position=position)
        except Exception:
            logger.warning("KINGDOM SETUP: kingdoms salon reorder failed", exc_info=True)


async def cleanup_legacy_structure(guild: discord.Guild, epoch_id: str = "") -> list[str]:
    """Retire the pre-restructuration salons and categories (best effort).

    Idempotent: missing salons/categories are simply skipped, the epoch
    channel is NEVER touched (it keeps its declared key, its name and
    category are managed by ``refresh_epoch_channel``). Returns the
    display names of what was removed.
    """
    epoch_channel_id = int(epoch_id) if epoch_id.isdigit() else 0
    removed = await _cleanup_legacy_salons(guild, epoch_channel_id)
    removed += await _cleanup_legacy_categories(guild, epoch_channel_id)
    await _reorder_kingdoms_salons(guild)
    return removed


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
        # The panels are pinned messages inside the structure channels
        # (kingdoms#138): provisioning the salons without re-pinning them
        # leaves the guild with no reachable enrollment/admin UI — the
        # setup must always end with functional panels.
        try:
            from kingdoms.mods.kingdoms.kingdom_panels import deploy_panels
            from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

            wiring = _wiring()
            await deploy_panels(
                guild,
                wiring.logs_service,
                wiring.bot_admins,
                wiring.mod_roles_service,
                wiring.kingdoms_service,
            )
        except Exception:
            logger.exception("KINGDOM SETUP: panel deploy failed for guild %s", guild.id)

        locale = str(interaction.locale) if interaction.locale else "en"
        view = build_setup_report_view(created, adopted, locale)
        await interaction.followup.send(view=view, ephemeral=True)
