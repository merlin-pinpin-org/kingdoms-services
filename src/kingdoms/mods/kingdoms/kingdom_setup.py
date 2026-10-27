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

import asyncio
import logging
import unicodedata
from typing import Any, cast

import discord
from discord import app_commands

from kingdoms.discord.commands_i18n import localized
from kingdoms.discord.ui import BLURPLE, Container, Separator, Text, UILayout

logger = logging.getLogger("kingdoms.kingdom")

MOD_NAME = "kingdoms"


_SMALL_CAPS = str.maketrans(
    {
        "ᴀ": "a", "ʙ": "b", "ᴄ": "c", "ᴅ": "d", "ᴇ": "e", "ꜰ": "f",
        "ɢ": "g", "ʜ": "h", "ɪ": "i", "ᴋ": "k", "ʟ": "l", "ᴍ": "m",
        "ɴ": "n", "ᴏ": "o", "ᴘ": "p", "ʀ": "r", "ᴛ": "t", "ᴜ": "u",
        "ᴠ": "v", "ᴡ": "w", "ʏ": "y", "ᴢ": "z", "ǫ": "q",
    }
)
"""ordre-salons-v1 (Drasah 19/10): the charte's small caps fold to plain
letters so a decorative category name (« ✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ ») slugs exactly
like its plain legacy form (« Conscription »)."""


def _slug(name: str) -> str:
    """Normalize a channel/category name to its structural slug.

    Discord lowercases, strips accents and turns spaces into dashes:
    "Âge sombre" is stored as "age-sombre". ordre-salons-v1 (Drasah
    19/10): the charte decorates salon/category names with an emoji
    prefix and small caps — the LOOKUP slug must ignore the decoration
    or every rename breaks the adoption-by-slug. Anything that is not
    an ASCII letter, digit or dash is dropped, so "🛒 marché" and
    "Marché" slug identically ("marche") before and after the charte.
    """
    translated = name.translate(_SMALL_CAPS)
    decomposed = unicodedata.normalize("NFKD", translated.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    kept = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "- ")) else "" for ch in without_accents
    )
    return kept.replace(" ", "-").strip("-")


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


_CHARTE_CATEGORY_DISPLAY: dict[str, str] = {
    "profils": "👤 ᴘʀᴏꜰɪʟs",
    "conscription": "✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ",
    # restructuration-gaia-v1 (Drasah 24/10): the Gaïa category
    # retires — the Kingdoms category absorbs its Territoire salon and
    # is renamed « Kingdoms-Gaia » (the Taverne is born here too).
    "kingdoms": "🏰 ᴋɪɴɢᴅᴏᴍs-ɢᴀɪᴀ",
    "champs-de-bataille": "⚔️ ᴄʜᴀᴍᴘs ᴅᴇ ʙᴀᴛᴀɪʟʟᴇ",
    "admin": "⚙️ ᴀᴅᴍɪɴ",
}
"""ordre-salons-v1 (Drasah 19/10): the grimoire charte — categories in
small caps with an emoji. Keyed by decoration-blind slug so a category
renamed by hand back to its plain form is re-caught by the next pass."""

_CHARTE_CHANNEL_DISPLAY: dict[str, str] = {
    "postuler": "📝 postuler",
    "candidatures": "📥 candidatures",
    "royaumes": "👑 royaumes",
    "scriptorium": "📜 scriptorium",
    "marche": "💰 marché",
    # marche-emoji-v1 (Drasah 26/10): le nom du salon reste « marché »,
    # seul l'emoji caddie 🛒 gênait — 💰 le remplace. La clé « bourse »
    # rattrape un salon déjà renommé « 💰 bourse » par la passe
    # précédente (migration inverse, id-mappée).
    "bourse": "💰 marché",
    # no geopolitics here: its live name is owned by refresh_age_pin
    # (the age emoji is dynamic) — the charte must never fight it.
    "territoire": "🗺️ territoire",
    "taverne": "🍺 taverne",
    "declarer-une-attaque": "⚔️ déclarer une attaque",
    "pourparlers": "🕊️ pourparlers",
    "parametres": "⚙️ paramètres",
    "gestion-saison": "🗓️ gestion saison",
    "machine-a-temps": "⏱️ machine à temps",
    "distributeur": "🎁 distributeur",
    # console-removal-v1 (Drasah 25/10): the 🛠 console-admin salon is
    # RETIRED — the listener never booted in prod (no add_listener),
    # the live flows live on the ⚙️ Paramètres panel. The slug joins
    # LEGACY_SALON_SLUGS below (swept at the next provisioning).
    "demandes": "📬 demandes",
}
"""ordre-salons-v1: the charte's salon display names (lowercase + one
emoji). Applied ONLY to salons living inside a structure category — a
realm salon named « Territoire » is renamed by kingdom_realms, never
here (its display happens to match anyway)."""

_CHARTE_TOPICS: dict[str, str] = {
    "postuler": "Le banc des recrues — chaque seigneur commence ici. Postule et rejoins un royaume.",
    "candidatures": "Les parchemins de candidature, triés par les hérauts du royaume.",
    "royaumes": "Le grand livre des royaumes — fondations, validations et refus.",
    "scriptorium": "La bibliothèque du royaume : cadastre, diplomatie et seigneurie y sont consignés.",
    "marche": "Les marchands croisent le fer — ressources, trocs et étals du royaume.",
    "bourse": "Les marchands croisent le fer — ressources, trocs et étals du royaume.",
    "territoire": "Les terres de Gaïa — son niveau d'éveil et le cadastre du monde.",
    "taverne": "Le feu crépite et les langues se délient — bois un coup, écoute les rumeurs et parie sur les prochaines batailles.",
    "declarer-une-attaque": "Le pas des bannières — déclare ta guerre, seigneur.",
    "pourparlers": "Sous la tente blanche : les négociations entre royaumes en guerre.",
    "parametres": "Les réglages du mod — réservés aux mains qui tirent les ficelles.",
    "gestion-saison": "Le sablier des saisons — ouverture, clôture et bascule.",
    "machine-a-temps": "Le mécanisme d'horloger — avancer l'âge du monde à des fins de test.",
    "distributeur": "La réserve secrète — distribuer des éléments à un royaume (test).",
    "demandes": "Les requêtes des seigneurs : modifications et départs.",
}
"""ordre-salons-v1: one micro-lore topic per structure salon. Keyed by
decoration-blind slug; applied idempotently (edit only on diff)."""

_CHARTE_DIVIDER_NAME = "┈┈ ✦ ┈┈"
"""ordre-salons-v1: the decorative divider salon (mute, read-only) —
its name slugs to the empty string, so it can never collide with a
declared channel."""

_CONSCRIPTION_ROLE_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("kingdoms_lord", "Kingdoms Lord"),
    ("kingdoms_king", "Kingdoms King"),
)

_PLAYER_CATEGORY_SLUGS: tuple[str, ...] = (
    "profils",
    # restructuration-gaia-v1 (Drasah 24/10): the renamed form leads;
    # the plain « kingdoms » covers a category renamed back by hand,
    # « gaia » covers a not-yet-migrated guild (decoration-blind slugs).
    "kingdoms-gaia",
    "kingdoms",
    "gaia",
    "champs-de-bataille",
)
"""permissions-serveur-v1 (Drasah 20/10): the player-facing categories gated
to @everyone — a newcomer sees only Beta and Conscription (headers included)."""

_PLAYER_ROLE_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("kingdoms_lord", "Kingdoms Lord"),
    ("kingdoms_king", "Kingdoms King"),
    ("kingdoms_admin", "Kingdoms Admin"),
)


async def _enforce_player_category_gating(guild: discord.Guild) -> None:
    """Hide the player categories from @everyone (idempotent, best effort).

    permissions-serveur-v1 (Drasah 20/10): the salons were already gated,
    but the category HEADERS stayed visible — a newcomer saw four empty
    categories in the sidebar. This pass denies @everyone view on the
    four player categories and unlocks them through the player roles
    (granted at acceptance). ONE ``edit(overwrites=...)`` call per
    category: editing overwrites is heavily rate-limited on the real
    API (lesson Check 89 — the realm incident of 2026-10-10).
    """
    roles = list(getattr(guild, "roles", []) or [])
    for slug in _PLAYER_CATEGORY_SLUGS:
        category = _find_category(guild, slug)
        if category is None:
            continue
        overwrites: dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=False, read_message_history=False
            )
        }
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            )
        for candidates in _PLAYER_ROLE_CANDIDATES:
            role = next((r for r in roles if str(getattr(r, "name", "")) in candidates), None)
            if role is None:
                continue
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True
            )
        try:
            await category.edit(overwrites=overwrites)
            logger.info("KINGDOM SETUP: player category gated %s", slug)
        except Exception:
            logger.warning("KINGDOM SETUP: player category gating failed %s", slug, exc_info=True)


async def _apply_structure_charte(guild: discord.Guild) -> None:
    """Rename the LIVE structure to the charte display names (idempotent).

    ordre-salons-v1 (Drasah 19/10): the core ChannelService adopts
    CATEGORIES by name — this pass must run BEFORE
    ``provision_mod_channels`` so the live « Conscription » becomes
    « ✒️ ᴄᴏɴsᴄʀɪᴘᴛɪᴏɴ » and the core adopts it (both slug to
    « conscription »), instead of creating a duplicate (the Check 87
    bug class). Salons are resolved by id in db, but renaming them here
    keeps a hard-reset's fresh provisioning and the live guild in
    sync. Geopolitique is skipped: its name carries the dynamic age
    emoji and belongs to ``refresh_age_pin``.
    """
    for category in list(getattr(guild, "categories", []) or []):
        naked = _slug(getattr(category, "name", ""))
        display = _CHARTE_CATEGORY_DISPLAY.get(naked)
        if display is not None and str(getattr(category, "name", "")) != display:
            try:
                await category.edit(name=display)
                logger.info("KINGDOM SETUP: category renamed to charte %r", display)
            except Exception:
                logger.warning("KINGDOM SETUP: category charte rename failed", exc_info=True)
    for channel in [*guild.text_channels, *(getattr(guild, "forums", None) or [])]:
        category = getattr(channel, "category", None)
        if category is None or _slug(getattr(category, "name", "")) not in _CHARTE_CATEGORY_DISPLAY:
            continue
        naked = _slug(getattr(channel, "name", ""))
        display = _CHARTE_CHANNEL_DISPLAY.get(naked)
        if display is not None and str(channel.name) != display:
            try:
                await channel.edit(name=display)
                logger.info("KINGDOM SETUP: salon renamed to charte %r", display)
            except Exception:
                logger.warning("KINGDOM SETUP: salon charte rename failed", exc_info=True)


async def _hide_conscription_from_players(guild: discord.Guild) -> None:
    """Deny the Conscription category to validated players (idempotent).

    ordre-salons-v1 (Drasah 19/10): once a player is accepted
    (Kingdoms Lord / Kingdoms King), Conscription is dead weight for
    him — the category is hidden from both player roles. The deny is
    additive on the roles, so it survives every re-provisioning.
    """
    category = _find_category(guild, "Conscription")
    if category is None:
        return
    roles = list(getattr(guild, "roles", []) or [])
    for candidates in _CONSCRIPTION_ROLE_CANDIDATES:
        role = next((r for r in roles if str(getattr(r, "name", "")) in candidates), None)
        if role is None:
            continue
        try:
            await category.set_permissions(role, overwrite=discord.PermissionOverwrite(view_channel=False))
        except Exception:
            logger.warning("KINGDOM SETUP: conscription hide failed", exc_info=True)


async def _ensure_charte_dividers(guild: discord.Guild) -> None:
    """Keep one decorative divider in Conscription and Battlefield (idempotent).

    ordre-salons-v1: the « ┈┈ ✦ ┈┈ » salon separates the panel zone
    from the war zone inside the two game categories. Mute: @everyone
    is denied send (the divider is scenery, not a salon to write in).

    reset-visibility-v1 (Drasah 27/10): a channel that carries an
    explicit @everyone view ALLOW stays visible to EVERY member — and
    a visible channel SURFACES its hidden category's header in the
    sidebar: the battlefield divider was the hole through which a
    role-less newcomer saw « ⚔️ Champs de Bataille ». The divider now
    MIRRORS its category's gating: hidden where the category denies
    @everyone (the player roles keep the scenery through their allow),
    public where the category is public — and the Conscription divider
    mirrors the immersion deny (Lord/King) instead.
    """
    for group in ("conscription", "champs-de-bataille"):
        category = _find_category(guild, _CHARTE_CATEGORY_DISPLAY[group])
        if category is None:
            continue
        divider = next(
            (
                c
                for c in getattr(category, "channels", []) or []
                if _slug(getattr(c, "name", "")) == ""
            ),
            None,
        )
        if divider is None:
            try:
                divider = await guild.create_text_channel(_CHARTE_DIVIDER_NAME, category=category)
            except Exception:
                logger.warning("KINGDOM SETUP: divider creation failed (%s)", group, exc_info=True)
                continue
        everyone = getattr(guild, "default_role", None)
        if everyone is not None:
            hidden = False
            category_lookup = getattr(category, "permission_overwrite_for", None)
            if category_lookup is not None:
                recorded = category_lookup(everyone)
                hidden = recorded is not None and recorded.view_channel is False
            desired_everyone = discord.PermissionOverwrite(
                view_channel=(False if hidden else True), send_messages=False
            )
            divider_lookup = getattr(divider, "permission_overwrite_for", None)
            recorded = divider_lookup(everyone) if divider_lookup is not None else None
            if recorded != desired_everyone:
                try:
                    await divider.set_permissions(everyone, overwrite=desired_everyone)
                except Exception:
                    logger.warning("KINGDOM SETUP: divider mute failed (%s)", group, exc_info=True)
        # the divider mirrors the category's role gating too (converge-skipped)
        role_candidates = (
            _PLAYER_ROLE_CANDIDATES
            if group == "champs-de-bataille"
            else _CONSCRIPTION_ROLE_CANDIDATES
        )
        for candidates in role_candidates:
            role = next(
                (r for r in (getattr(guild, "roles", []) or []) if str(getattr(r, "name", "")) in candidates),
                None,
            )
            if role is None:
                continue
            desired_role = (
                discord.PermissionOverwrite(view_channel=True, read_message_history=True)
                if group == "champs-de-bataille"
                else discord.PermissionOverwrite(view_channel=False)
            )
            divider_lookup = getattr(divider, "permission_overwrite_for", None)
            recorded = divider_lookup(role) if divider_lookup is not None else None
            if recorded != desired_role:
                try:
                    await divider.set_permissions(role, overwrite=desired_role)
                except Exception:
                    logger.warning(
                        "KINGDOM SETUP: divider role gating failed (%s)", group, exc_info=True
                    )


async def _apply_charte_topics(guild: discord.Guild) -> None:
    """Apply the micro-lore topics (idempotent — edit only on diff)."""
    for channel in [*guild.text_channels, *(getattr(guild, "forums", None) or [])]:
        category = getattr(channel, "category", None)
        if category is None or _slug(getattr(category, "name", "")) not in _CHARTE_CATEGORY_DISPLAY:
            continue
        topic = _CHARTE_TOPICS.get(_slug(getattr(channel, "name", "")))
        if topic is None or getattr(channel, "topic", None) == topic:
            continue
        try:
            await channel.edit(topic=topic)
        except Exception:
            logger.warning("KINGDOM SETUP: topic charte failed", exc_info=True)


async def _enforce_category_order(guild: discord.Guild) -> None:
    """Force Drasah's sidebar order (idempotent, best effort).

    ordre-salons-v1 (Drasah 19/10): Beta → Conscription → Champs de
    Bataille → Kingdoms → Gaïa → the player realms (current relative
    order preserved) → Admin ALWAYS behind the last realm → Profils
    at the very bottom. Beta is Drasah's hand-made category (skipped
    silently when absent). A freshly created realm category falls to
    the bottom of Discord's sidebar — this pass re-runs after every
    realm provisioning to push Admin (and Profils) back in place.
    """
    categories = list(getattr(guild, "categories", []) or [])
    structure_slugs = set(_CHARTE_CATEGORY_DISPLAY)
    by_slug: dict[str, discord.CategoryChannel] = {}
    realms: list[discord.CategoryChannel] = []
    for category in categories:
        raw_name = str(getattr(category, "name", ""))
        slug = _slug(raw_name)
        is_realm = raw_name.startswith("🏰 ") and slug not in structure_slugs and slug != "gaia"
        if is_realm:
            realms.append(category)
        elif slug not in by_slug:
            by_slug[slug] = category
    # the realms keep their current relative order (Discord drops a
    # fresh category at the bottom of the sidebar)
    realms.sort(key=lambda c: getattr(c, "position", 0))
    # restructuration-gaia-v1 (Drasah 24/10): « kingdoms-gaia » leads;
    # the plain « kingdoms » and « gaia » slugs cover a guild the charte
    # pass has not renamed yet (idempotent, both filtered by presence).
    ordered: list[discord.CategoryChannel] = [
        by_slug[slug]
        for slug in ("beta", "conscription", "champs-de-bataille", "kingdoms-gaia", "kingdoms", "gaia")
        if slug in by_slug
    ]
    ordered += realms
    if "admin" in by_slug:
        ordered.append(by_slug["admin"])  # ALWAYS behind the last realm
    for position, category in enumerate(ordered):
        if getattr(category, "position", 0) != position:
            try:
                await category.edit(position=position)
            except Exception:
                logger.warning("KINGDOM SETUP: category order failed", exc_info=True)
    # Profils reads LAST — below everything, realms and admin included.
    profiles = by_slug.get("profils")
    if profiles is not None and getattr(profiles, "position", 0) != len(ordered):
        try:
            await profiles.edit(position=len(ordered))
        except Exception:
            logger.warning("KINGDOM SETUP: profiles bottom order failed", exc_info=True)


async def _migrate_gaia_into_kingdoms(guild: discord.Guild) -> None:
    """Move the Gaïa salons into Kingdoms-Gaia, then delete the category.

    restructuration-gaia-v1 (Drasah 24/10): the Gaïa category retires —
    its children (in practice the 🗺️ Territoire salon) join the renamed
    Kingdoms-Gaia category. This runs AFTER the charte pass renamed the
    live « Kingdoms » to « Kingdoms-Gaia » and BEFORE the core
    provisions: the core adopts a channel by name INSIDE its declared
    group, so a territory salon still sitting in the old Gaïa category
    would be DUPLICATED (the Check 87 bug class) and the legacy purge
    would then kill the orphan. Moving first makes the core adopt the
    very channel (id and pinned views preserved). Idempotent: a guild
    without a Gaïa category is a silent no-op; a move failure leaves
    the category in place for the next pass (the purge never touches it).
    """
    kingdoms = _find_category(guild, "Kingdoms-Gaia") or _find_category(guild, "Kingdoms")
    gaia = _find_category(guild, "Gaïa")
    if kingdoms is None or gaia is None or gaia.id == kingdoms.id:
        return
    for channel in list(getattr(gaia, "channels", []) or []):
        try:
            await channel.edit(category=kingdoms)
            logger.info("KINGDOM SETUP: gaia salon %s moved into Kingdoms-Gaia", channel.name)
        except Exception:
            logger.warning(
                "KINGDOM SETUP: gaia salon move failed (%s) — category kept for retry",
                getattr(channel, "name", "?"),
                exc_info=True,
            )
            return
    try:
        await gaia.delete()
        logger.info("KINGDOM SETUP: retired Gaïa category deleted")
    except Exception:
        logger.warning("KINGDOM SETUP: retired Gaïa category delete failed", exc_info=True)


_PROVISION_LOCKS: dict[int, asyncio.Lock] = {}


async def provision_structure(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """Provision the declared salons-first structure through the core.

    Serialised per guild: concurrent triggers (startup provisioning,
    /kingdom, 🚀 Déployer clicks) used to stack parallel Discord API
    storms — a second run now waits for the first (idempotent, cheap
    once converged) instead of doubling the rate-limit pressure
    (rate-limit lesson Checks 89/92, livelock 24/10).
    """
    lock = _PROVISION_LOCKS.setdefault(guild.id, asyncio.Lock())
    if lock.locked():
        logger.info("KINGDOM SETUP: provisioning already running for guild %s — waiting", guild.id)
    async with lock:
        return await _provision_structure_locked(guild)


async def _provision_structure_locked(guild: discord.Guild) -> tuple[list[str], list[str]]:
    """The actual provisioning flow (called under the guild lock).

    The declaration (``config/mods/kingdoms.yaml``) is the single source
    of truth: groups in declaration order, then each group's channels —
    kinds, admin-only flags and positions are data. Existing channels
    are adopted (never duplicated); re-running is safe. Returns the
    (created, adopted) display paths from the provision report.

    ordre-salons-v1 (Drasah 19/10): the charte pass renames the LIVE
    structure to the declared display names BEFORE the core provisions —
    the core adopts categories by name, so renaming after (or never)
    would duplicate every decorated category (the Check 87 bug class).
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    channel_service = wiring.channel_service
    if channel_service is None:
        raise RuntimeError("the ChannelService is not wired — the salons-first bootstrap needs the platform services")
    await _apply_structure_charte(guild)
    # restructuration-gaia-v1 (Drasah 24/10): the Gaïa children move
    # into the renamed Kingdoms-Gaia category BEFORE the core provisions
    # (the core adopts by name INSIDE the declared group — moving after
    # would duplicate the Territoire salon).
    await _migrate_gaia_into_kingdoms(guild)
    report = await channel_service.provision_mod_channels(str(guild.id), MOD_NAME)
    # permissions-serveur-v1: the Admin category stays hidden from
    # non-admins even when it was adopted long before the flag.
    await _enforce_admin_category_deny(guild)
    # permissions-serveur-v1 (20/10): the player category HEADERS are
    # gated too — a newcomer sees only Beta and Conscription.
    await _enforce_player_category_gating(guild)
    # console-removal-v1 (Drasah 25/10): the console welcome block is
    # GONE — the salon itself is swept by cleanup_legacy_structure (its
    # slug sits in LEGACY_SALON_SLUGS).
    await cleanup_legacy_structure(guild)
    # ordre-salons-v1 (Drasah 19/10): the sidebar reads like the game —
    # conscription hides from validated players, the decorative
    # dividers, the micro-lore topics and the category order apply on
    # every provisioning pass (idempotent, best effort).
    await _hide_conscription_from_players(guild)
    # immersion-v1 (Drasah 20/10): the out-of-game categories (Beta,
    # Conscription) hide from every in-game role — the immersion is the
    # default; the member opts back in from his realm's user settings.
    from kingdoms.mods.kingdoms.kingdom_immersion import apply_immersion_gating

    await apply_immersion_gating(guild)
    # taverne-immersion-v1 (Drasah 26/10): the private Taverne
    # category (Comptoir salon) — a lord entering the taverne sees
    # ONLY it; the zone self-heals on every pass (role, category,
    # salon, denies) and the member always has his Sortir button.
    from kingdoms.mods.kingdoms.tavern_immersion import (
        ensure_tavern_immersion_structure,
    )

    try:
        await ensure_tavern_immersion_structure(guild, "fr")
    except Exception:
        logger.warning(
            "KINGDOM SETUP: taverne immersion structure failed", exc_info=True
        )
    await _ensure_charte_dividers(guild)
    await _apply_charte_topics(guild)
    await _enforce_category_order(guild)
    # mercenary-beta-refresh-v1 (Drasah 25/10): the Chief's taverne panel
    # is (re)rendered on every provisioning pass (boot, /kingdom, 🚀
    # Déployer) — the beta call button rides the view only while a beta
    # is armed/running, and the panel used to wait for the next 20h
    # market open to pick it up.
    try:
        from kingdoms.mods.kingdoms.kingdom_mercenary_panel import (
            refresh_mercenary_panels_safe,
        )

        await refresh_mercenary_panels_safe(guild, "fr")
    except Exception:
        logger.warning("KINGDOM SETUP: mercenary panel refresh failed", exc_info=True)
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
    # restructuration-gaia-v1 (Drasah 24/10): the Taverne is REBORN in
    # the Kingdoms-Gaia category — it left the purge pool (the old
    # general-group Taverne is already gone from the live guild).
    # permissions-serveur-v1 (Drasah 19/10): the dedicated 🏺 Âge salon
    # retires — the age (emoji + pinned message) lives in Géopolitique.
    # ordre-salons-v1: the slug is decoration-blind — the retired salon
    # « 🏺 Âge » now slugs to « age ».
    "age",
    # ordre-salons-v1 (Drasah 19/10): the Support group retires — the
    # question / signaler-un-bug forums read as admin duplicates and
    # the anonymous Pigeon handles their absence gracefully.
    "question",
    "signaler-un-bug",
    # console-removal-v1 (Drasah 25/10): the 🛠 console-admin salon
    # retires — the console listener never booted in prod, the live
    # flows live on the ⚙️ Paramètres panel; the beta taverne button
    # calls the Mercenary Chief (chef-mercenaire-v1 T3).
    "console-admin",
)
"""The retired salons-first rubric salons (patch restructuration-royaume):
their content moved into the Scriptorium ephemeral rubrics — plus the
retired Champs de bataille salons (restructuration-serveur-v1 b): the
delays moved into the declare flow's « Itinéraire » line and the
announcements into each attack's OWN salon (the per-attack salons carry
long slugs, never the bare « attaques » — safe to purge), the retired
Gaïa salons (restructuration-serveur-v1 c), the retired Général salons
(restructuration-serveur-v1 d: rules/presentation live in the
Scriptorium buttons; saison/update/annonce/suggestion go away). The
Taverne LEFT this pool: restructuration-gaia-v1 (Drasah 24/10) re-creates
it inside the Kingdoms-Gaia category (the purge would delete the fresh
salon right after provisioning)."""

LEGACY_CATEGORY_NAMES: tuple[str, ...] = ("Époque", "Scriptorium", "Général", "Support")
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
    "📜 scriptorium",
    "💰 marché",
    "🌍 géopolitique",
    "🗺️ territoire",
    "🍺 taverne",
)
"""Drasah's chosen order of the Kingdoms-Gaia-category salons
(permissions-serveur-v1: the 🏺 Âge salon retired — the age lives in
Géopolitique's pinned message; restructuration-gaia-v1 24/10: Territoire
moved in from the retired Gaïa category, the Taverne closes the row)."""


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
    """Force Drasah's order of the Kingdoms-Gaia-category salons."""
    # restructuration-gaia-v1: the category slugs « kingdoms-gaia »
    # after the rename — the plain form covers a hand-renamed guild.
    category = _find_category(guild, "Kingdoms-Gaia") or _find_category(guild, "Kingdoms")
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