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
from typing import Any, cast

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


EPOCH_CHANNEL_KEY = "epoch"
"""permissions-serveur-v1 (Drasah 19/10): the dedicated 🏺 Âge salon is
RETIRED — the age lives in Géopolitique (name emoji + pinned message).
The key stays ONLY as the declared-channel lookups' legacy: the channel
is swept by ``LEGACY_SALON_SLUGS`` at the next provisioning."""


async def _enforce_admin_category_deny(guild: discord.Guild) -> None:
    """Hide the Admin category from non-admins (best effort, idempotent).

    permissions-serveur-v1 (Drasah 19/10): the admin category must be
    masked for @everyone. Creation-time overwrites only apply to CREATED
    categories — an adopted one (provisioned before the flag) keeps its
    legacy overwrites, so the deny is re-applied at every bootstrap. The
    bot keeps its explicit allow (lesson Check 75: it is no admin).
    """
    category = _find_category(guild, "Admin")
    if category is None:
        return
    try:
        await category.set_permissions(
            guild.default_role, overwrite=discord.PermissionOverwrite(view_channel=False)
        )
        if guild.me is not None:
            await category.set_permissions(
                guild.me,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True
                ),
            )
    except Exception:
        logger.warning("KINGDOM SETUP: admin category deny failed", exc_info=True)


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
    # permissions-serveur-v1: the Admin category stays hidden from
    # non-admins even when it was adopted long before the flag.
    await _enforce_admin_category_deny(guild)
    # admin-console-v1: the console salon gets its pinned welcome message.
    console_id = report.channel_ids.get("console")
    console_channel = guild.get_channel(int(console_id)) if console_id and console_id.isdigit() else None
    if isinstance(console_channel, discord.TextChannel):
        from kingdoms.mods.kingdoms.admin_console import ensure_console_welcome

        await ensure_console_welcome(console_channel)
    await cleanup_legacy_structure(guild)
    return list(report.created), list(report.adopted)


LEGACY_SALON_SLUGS: tuple[str, ...] = (
    "seigneurs",
    "diplomatie",
    "cadastre",
    "delais-attaque",
    "attaques",
    # restructuration-serveur-v1 c (Drasah 19/10): the Gaïa category
    # keeps ONE salon — the AI level moved into the Territoire view and
    # the FFA lives in an ephemeral salon opened when it manifests.
    "patrouille",
    "exploration",
    # restructuration-serveur-v1 d (Drasah 19/10): Général shrinks to
    # the Taverne — the pitch and the rules moved into the Scriptorium's
    # ephemeral 📖 Règles / 🎤 Présentation buttons.
    "presentation",
    "annonce",
    "regles",
    "saison",
    "update",
    "suggestion",
    # restructuration-serveur-v1 e (Drasah 19/10): the general group
    # disappears ENTIRELY — the Taverne leaves with its category.
    "taverne",
    # permissions-serveur-v1 (Drasah 19/10): the dedicated 🏺 Âge salon
    # retires — the age (emoji + pinned message) lives in Géopolitique.
    "🏺-age",
)
"""The retired salons-first rubric salons (patch restructuration-royaume):
their content moved into the Scriptorium ephemeral rubrics — plus the
retired Champs de bataille salons (restructuration-serveur-v1 b): the
delays moved into the declare flow's « Itinéraire » line and the
announcements into each attack's OWN salon (the per-attack salons carry
long slugs, never the bare « attaques » — safe to purge), the retired
Gaïa salons (restructuration-serveur-v1 c), the retired Général salons
(restructuration-serveur-v1 d: rules/presentation live in the
Scriptorium buttons; saison/update/annonce/suggestion go away) and the
Taverne (restructuration-serveur-v1 e: the general group is gone)."""

LEGACY_CATEGORY_NAMES: tuple[str, ...] = ("Époque", "Scriptorium", "Général")
"""The retired group categories — the epoch salon now lives under
Kingdoms (then retired with it — permissions-serveur-v1), and the
general group disappeared entirely
(restructuration-serveur-v1 e, Drasah 19/10: Taverne + the vocal go
with the category)."""

LEGACY_BATTLE_CATEGORY_NAMES: tuple[str, ...] = ("Champs de bataille", "Battlefields")
"""The battle-room duplicate categories (battle-room-categorie-v1,
Drasah 19/10): the old room flow created one at the first battle — its
exact-name lookup on « Champs de bataille » never matched the
provisioned « Champs de Bataille ». EXACT names only: the provisioned
category's display name is « Champs de Bataille » and must be spared
(its slug is case-insensitively identical to the duplicate's)."""

KINGDOMS_CATEGORY_ORDER: tuple[str, ...] = (
    "📜 Scriptorium",
    "Marché",
    "Géopolitique",
)
"""Drasah's chosen order of the Kingdoms-category salons
(permissions-serveur-v1: the 🏺 Âge salon retired — the age lives in
Géopolitique's pinned message)."""


async def _cleanup_legacy_salons(guild: discord.Guild, epoch_channel_id: int) -> list[str]:
    """Delete the retired rubric salons (Seigneurs/Diplomatie/Cadastre)."""
    removed: list[str] = []
    # restructuration-serveur-v1 d (Drasah 19/10): the retired « Règles »
    # and « Suggestion » are FORUM channels — the purge pool must read
    # guild.forums too, not only the text channels.
    pool: list[Any] = [*guild.text_channels, *(getattr(guild, "forums", None) or [])]
    for slug in LEGACY_SALON_SLUGS:
        for channel in [c for c in pool if _slug(c.name) == slug]:
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


async def _cleanup_legacy_battle_categories(guild: discord.Guild) -> list[str]:
    """Delete the battle-room duplicate categories (children first).

    battle-room-categorie-v1 (Drasah 19/10): the old room flow created
    a « Champs de bataille » duplicate at the first battle (an exact
    lookup that never matched the provisioned « Champs de Bataille »).
    The duplicate only ever held battle voice rooms — its children go
    with it. EXACT name match only: the provisioned « Champs de
    Bataille » (different case) is never touched.
    """
    removed: list[str] = []
    for category in list(getattr(guild, "categories", []) or []):
        if str(getattr(category, "name", "")) not in LEGACY_BATTLE_CATEGORY_NAMES:
            continue
        for channel in list(getattr(category, "channels", []) or []):
            try:
                await channel.delete()
                removed.append(str(channel.name))
            except Exception:
                logger.warning("KINGDOM SETUP: legacy battle room removal failed", exc_info=True)
        try:
            await category.delete()
            removed.append(str(category.name))
        except Exception:
            logger.warning("KINGDOM SETUP: legacy battle category removal failed", exc_info=True)
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

    Idempotent: missing salons/categories are simply skipped. The
    ``epoch_id`` spare is a legacy no-op kept for the call sites'
    signature (permissions-serveur-v1: the 🏺 Âge salon is retired like
    the other legacy salons — nothing is spared anymore). Returns the
    display names of what was removed.
    """
    epoch_channel_id = int(epoch_id) if epoch_id.isdigit() else 0
    removed = await _cleanup_legacy_salons(guild, epoch_channel_id)
    removed += await _cleanup_legacy_categories(guild, epoch_channel_id)
    removed += await _cleanup_legacy_battle_categories(guild)
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
