"""Per-kingdom Discord structures: the « Royaume [Nom] » slices (rule 35, D70).

Two halves:

- The **Royaumes panel** (pinned in the admin ``royaumes`` channel):
  every kingdom of the season with its validation state (✅ validé /
  ⏳ en attente / ❌ refusé), its King and its Lords, plus the admin
  action buttons (validate / refuse / rename). Restart-proof through
  DynamicItem custom_ids, like every other panel.
- The **realm structure provisioning**: an APPROVED kingdom gets a
  private category ``Royaume [Nom]`` with its 8 salons. The YAML
  structure is static data — a per-kingdom category cannot be, so this
  module provisions it directly, idempotently (adopt-by-slug, never a
  duplicate). ``Territoire`` and ``Alliances`` stay readable by every
  lord (the civilizations/map overview is public to players); the rest
  is visible only to the kingdom's members and the admins.

A kingdom without a King (admin-created, imposed) is a first-class
case: the structure exists, the members list may grow later.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

# alliances-single-message-v1 (Drasah 29/10): the standalone draft
# starter is retired — the unified alliances-panel view is the single
# source of the hand, its mosaic is built by kingdom_realm_content.
from kingdoms.mods.kingdoms.kingdom_setup import _slug
from kingdoms.mods.kingdoms.panel_messages import NO_MENTIONS, channel_messages, message_text

logger = logging.getLogger("kingdoms.kingdom_realms")

REALMS_PANEL_MARKER = "kingdoms:panel:royaumes"
REALMS_CHANNEL = "Royaumes"
REALM_SALONS: tuple[tuple[str, str], ...] = (
    ("salle-du-conseil", "💬 salle du conseil"),
    ("le-royaume", "🏰 le-royaume"),
    ("territoire", "🗺️ territoire"),
    ("alliances", "🤝 alliances"),
    ("eglise", "⛪ paroisse"),
)
# ordre-salons-v1 (Drasah 19/10): the grimoire charte reaches the realm
# salons — lowercase + one emoji, and the lookups stay decoration-blind
# (kingdom_setup._slug), so panels and content keep resolving after
# the rename. The adoption below renames a live pre-patch salon to the
# charte display (never duplicates it).
# Drasah 19/10 (restructuration-serveur-v1): the realm shrinks to 5
# salons — Remparts and Seigneurie become EPHEMERAL BUTTONS on the
# Le-Royaume view, the Pigeon-Voyageur salon had no wiring at all.
# These three are DELETED at the next provisioning pass.
REMOVED_REALM_SALONS: tuple[tuple[str, str], ...] = (
    ("remparts", "🧱 Remparts"),
    ("seigneurs", "Seigneurs"),
    ("pigeon-voyageur", "Pigeon-Voyageur"),
)
# Drasah 2026-10-11: the salon « Église » is renamed « Paroisse » (the
# palier lives inside); the data key ``eglise`` never changes.
# Drasah 2026-10-14 (marche-v2 article 7): the salon « Patrouille » is
# renamed « 🧱 Remparts » (the Garde Royale is piloted inside); the data
# key ``patrouille`` never changes.
LEGACY_REALM_SALON_NAMES: dict[str, str] = {"eglise": "Église", "patrouille": "Patrouille"}
PUBLIC_REALM_SALONS = {"territoire", "alliances"}

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "realms_title": "🏰 Kingdoms validation",
        "realms_empty": "No kingdom yet — founding Kings appear here as pending.",
        "realms_pending": "Pending",
        "realms_approved": "Validated",
        "realms_refused": "Refused",
        "realms_king_none": "no King yet",
        "realms_approve_button": "✅ Validate",
        "realms_refuse_button": "❌ Refuse",
        "realms_rename_button": "✏️ Rename",
        "realms_rename_title": "Rename the kingdom",
        "realms_reking_button": "👑 Change the King",
        "realms_reking_title": "👑 Who takes the throne of this kingdom?",
        "realms_reking_empty": "No active lord to crown in this kingdom.",
        "realms_reking_done": "👑 The throne of **{kingdom}** passes to **{lord}**.",
        "realms_rename_field": "New name",
        "realms_done": "Done.",
        "realms_failed": "Action failed: {error}",
        "realms_delete_button": "🗑️ Delete",
        "realms_delete_confirm_title": "Delete the kingdom?",
        "realms_delete_confirm_hint": (
            "This removes the kingdom, its members, its territories and its "
            "Discord salons. The refusal trace does not survive a deletion."
        ),
        "realms_delete_confirm_button": "🗑️ Delete for good",
        "realms_delete_cancel_button": "Cancel",
        "realms_cancelled": "Cancelled.",
        "realms_deleted": "Kingdom deleted.",
    },
    "fr": {
        "realms_title": "🏰 Validation des royaumes",
        "realms_empty": "Aucun royaume — les Rois fondateurs apparaissent ici en attente.",
        "realms_pending": "En attente",
        "realms_refused": "Refusé",
        "realms_approved": "Validé",
        "realms_king_none": "pas de Roi pour l'instant",
        "realms_approve_button": "✅ Valider",
        "realms_refuse_button": "❌ Refuser",
        "realms_rename_button": "✏️ Renommer",
        "realms_rename_title": "Renommer le royaume",
        "realms_reking_button": "👑 Changer le roi",
        "realms_reking_title": "👑 Qui prend le trône de ce royaume ?",
        "realms_reking_empty": "Aucun seigneur actif à couronner dans ce royaume.",
        "realms_reking_done": "👑 Le trône de **{kingdom}** passe à **{lord}**.",
        "realms_rename_field": "Nouveau nom",
        "realms_done": "C'est fait.",
        "realms_failed": "L'action a échoué : {error}",
        "realms_delete_button": "🗑️ Supprimer",
        "realms_delete_confirm_title": "Supprimer le royaume ?",
        "realms_delete_confirm_hint": (
            "Cela supprime le royaume, ses membres, ses territoires et ses "
            "salons Discord. Aucune trace ne survit à une suppression."
        ),
        "realms_delete_confirm_button": "🗑️ Supprimer définitivement",
        "realms_delete_cancel_button": "Annuler",
        "realms_cancelled": "Annulé.",
        "realms_deleted": "Royaume supprimé.",
    },
}

STATUS_EMOJI = {"pending": "⏳", "approved": "✅", "refused": "❌"}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


GAIA_KINGDOM_DISPLAY = "Gaïa"


def realm_category_name(kingdom_name: str) -> str:
    """Return the display name of a kingdom's category: « 🏰 [Nom] ».

    ordre-salons-v1 (Drasah 19/10): the grimoire charte — a realm
    category carries the same 🏰 as the structure's Kingdoms/Gaïa
    categories, so it slugs to the bare kingdom name. The legacy
    « Royaume [Nom] » form is still adopted (dual match in
    ``_find_realm_category``) and renamed at the next provisioning.
    """
    return f"🏰 {kingdom_name}"


def _realm_status_lines(
    by_state: dict[str, list[Any]],
    lords_by_kingdom: dict[str, list[Any]],
    strings: dict[str, str],
) -> list[discord.ui.TextDisplay[discord.ui.LayoutView]]:
    """One status line per kingdom, grouped pending → refused → approved."""
    lines: list[discord.ui.TextDisplay[discord.ui.LayoutView]] = []
    for state in ("pending", "refused", "approved"):
        for kingdom in by_state.get(state, []):
            members = lords_by_kingdom.get(kingdom.id, [])
            king = next((m for m in members if str(m.role) == "king"), None)
            king_label = king.display_name if king is not None else strings["realms_king_none"]
            lord_names = ", ".join(m.display_name for m in members if str(m.role) != "king")
            line = (
                f"{STATUS_EMOJI[state]} **{kingdom.name}** — {strings[f'realms_{state}']} · "
                f"👑 {king_label}"
            )
            if lord_names:
                line += f" · 🛡️ {lord_names}"
            lines.append(discord.ui.TextDisplay(line[:4000]))
    return lines


def _validation_rows(
    non_gaia: list[Any],
    strings: dict[str, str],
) -> list[discord.ui.ActionRow[discord.ui.LayoutView]]:
    """One button row per kingdom: validation buttons + 🗑️ delete (never empty)."""
    buttons = (
        ("approve", strings["realms_approve_button"], discord.ButtonStyle.success),
        ("refuse", strings["realms_refuse_button"], discord.ButtonStyle.danger),
        ("rename", strings["realms_rename_button"], discord.ButtonStyle.secondary),
    )
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    for kingdom in non_gaia:
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        kid = str(kingdom.id)[:90]
        if str(kingdom.validation) != "approved":
            for action, label, style in buttons:
                row.add_item(
                    discord.ui.Button(
                        label=label[:80],
                        style=style,
                        custom_id=f"kingdoms:realm:{action}:{kid}",
                    )
                )
        row.add_item(
            discord.ui.Button(
                label=strings["realms_delete_button"][:80],
                style=discord.ButtonStyle.danger,
                custom_id=f"kingdoms:realm:delete:{kid}",
            )
        )
        # admin-tools-v1 (Drasah 30/10): the 👑 reking button rides every
        # non-Gaïa kingdom — approved included: the throne may change
        # hands at ANY point of the season, not only at the validation.
        row.add_item(
            discord.ui.Button(
                label=strings["realms_reking_button"][:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"kingdoms:realm:reking:{kid}",
            )
        )
        rows.append(row)
    return rows


def build_realms_panel(locale: str, kingdoms: list[Any], lords: list[Any]) -> discord.ui.LayoutView:
    """Build the Royaumes panel: every kingdom, its state, its people.

    Pending/refused kingdoms get the validation buttons; every kingdom
    gets the 🗑️ delete button (a hard delete, distinct from refuse).
    """
    strings = _strings(locale)
    non_gaia = [k for k in kingdoms if not k.is_gaia]
    by_state: dict[str, list[Any]] = {"pending": [], "approved": [], "refused": []}
    for kingdom in non_gaia:
        by_state.setdefault(str(kingdom.validation), []).append(kingdom)
    lords_by_kingdom: dict[str, list[Any]] = {}
    for lord in lords:
        if getattr(lord, "kingdom_id", None) is not None and not lord.left:
            lords_by_kingdom.setdefault(lord.kingdom_id, []).append(lord)

    lines = _realm_status_lines(by_state, lords_by_kingdom, strings)

    container_children: list[Any] = [
        discord.ui.TextDisplay(f"# {strings['realms_title']}"),
        discord.ui.Separator(),
    ]
    if not lines:
        container_children.append(discord.ui.TextDisplay(strings["realms_empty"]))
    else:
        container_children.extend(lines)

    rows = _validation_rows(non_gaia, strings)
    container_children.append(discord.ui.Separator())
    if rows:
        container_children.extend(rows)
    container_children.append(discord.ui.TextDisplay(f"-# {REALMS_PANEL_MARKER}"))

    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*container_children, accent_colour=discord.Colour.gold()))
    return view


def _find_realm_category(guild: discord.Guild, kingdom_name: str) -> discord.CategoryChannel | None:
    """Find a kingdom's category by slug (read-only lookup).

    ordre-salons-v1: dual match — the charte « 🏰 Nom » (naked slug)
    and the legacy « Royaume Nom » (slug « royaume-nom ») resolve to
    the same category, so a pre-patch realm is adopted then renamed.
    """
    wanted = _slug(realm_category_name(kingdom_name))
    legacy = _slug(f"Royaume {kingdom_name}")
    for category in getattr(guild, "categories", []):
        slug = _slug(getattr(category, "name", ""))
        if slug == wanted or slug == legacy:
            return category  # type: ignore[no-any-return]
    return None


def _member_role(guild: discord.Guild, role_key: str) -> discord.Role | None:
    """Find a mod role (kingdoms_lord / kingdoms_admin) by name."""
    for role in getattr(guild, "roles", []):
        if getattr(role, "name", "") == role_key:
            return role  # type: ignore[no-any-return]
    return None


async def _realm_overwrites(
    guild: discord.Guild,
    member_ids: list[int],
) -> dict[Any, discord.PermissionOverwrite]:
    """Build the full overwrite set of a realm category in one pass.

    One single dict applied with ONE API call (at creation or on
    re-sync): editing channel overwrites is heavily rate-limited on the
    real API (drasah live incident 2026-10-10 — a category appeared
    with no salons while the bot crawled through 4-6 separate
    ``set_permissions`` PATCHes). The bot itself is granted access:
    without it the @everyone deny below locks the bot out (403 Missing
    Access, code 50001) and every state view deployment fails silently.
    """
    overwrites: dict[Any, discord.PermissionOverwrite] = {}
    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        overwrites[everyone] = discord.PermissionOverwrite(
            view_channel=False, read_message_history=False
        )
    bot_member = getattr(guild, "me", None)
    if bot_member is not None:
        overwrites[bot_member] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
            embed_links=True,
            attach_files=True,
        )
    admin_role = _member_role(guild, "kingdoms_admin")
    if admin_role is not None:
        overwrites[admin_role] = discord.PermissionOverwrite(
            view_channel=True, manage_channels=True
        )
    for member_id in member_ids:
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except Exception:
                member = None
        if member is None:
            # Live incident 2026-10-18: a claimed King was silently
            # absent from his own category — this must be loud.
            logger.warning(
                "KINGDOM REALMS: member %s skipped from overwrites (unresolvable)", member_id
            )
            continue
        if member is not None:
            # Drasah's live finding (2026-10-12): view_channel alone is
            # NOT enough — an unspecified permission falls back to the
            # @everyone deny above, so the members saw the salons but
            # not their content ("not authorised to read the history").
            overwrites[member] = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True
            )
    return overwrites


async def _apply_realm_overwrites(guild: discord.Guild, category: Any, member_ids: list[int]) -> None:
    """Apply the realm overwrite set in a single API call (edit-or-create)."""
    overwrites = await _realm_overwrites(guild, member_ids)
    edit = getattr(category, "edit", None)
    if edit is not None:
        try:
            await edit(overwrites=overwrites)
        except TypeError:
            # a mock without the overwrites kwarg: fall back per-target
            for target, overwrite in overwrites.items():
                await category.set_permissions(target, overwrite=overwrite)
    else:
        for target, overwrite in overwrites.items():
            await category.set_permissions(target, overwrite=overwrite)
    # taverne-resync-v1 (Drasah 30/10): the replace-edit just wiped the
    # member denies of every tavern insider — re-pose them right away.
    from kingdoms.mods.kingdoms.tavern_immersion import reapply_tavern_member_denies_safe

    await reapply_tavern_member_denies_safe(guild, [category])


async def grant_bot_access_to_category(guild: discord.Guild, category: Any) -> None:
    """Best-effort grant of the bot's own access to a category.

    Categories created BEFORE the bot-access fix carry no bot overwrite:
    the bot cannot even delete them (403 Missing Access on every
    category deletion — drasah live incident 2026-10-10). Granting the
    bot view + manage first makes every delete path work again.
    """
    bot_member = getattr(guild, "me", None)
    if bot_member is None:
        return
    try:
        await category.set_permissions(
            bot_member,
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
            manage_messages=True,
        )
    except Exception:
        logger.warning("KINGDOM REALMS: bot access grant failed", exc_info=True)


async def _adopt_realm_salon(
    guild: discord.Guild,
    category: Any,
    key: str,
    name: str,
) -> Any | None:
    """Find a realm salon (or its legacy name) and rename it to the charte.

    ordre-salons-v1 (Drasah 19/10): adoption is decoration-blind
    (``_slug``), so a pre-patch « Territoire » is found by the charte
    name « 🗺️ territoire » and RENAMED, never duplicated. The legacy
    Église/Patrouille renames keep working through
    ``LEGACY_REALM_SALON_NAMES``.
    """
    existing = next(
        (c for c in getattr(category, "channels", []) if _slug(getattr(c, "name", "")) == _slug(name)),
        None,
    )
    if existing is None and key in LEGACY_REALM_SALON_NAMES:
        legacy_name = LEGACY_REALM_SALON_NAMES[key]
        existing = next(
            (
                c
                for c in getattr(category, "channels", [])
                if _slug(getattr(c, "name", "")) == _slug(legacy_name)
            ),
            None,
        )
    if existing is not None and str(getattr(existing, "name", "")) != name:
        try:
            await existing.edit(name=name)
            logger.info("KINGDOM REALMS: salon renamed to charte %s", name)
        except Exception:
            logger.warning("KINGDOM REALMS: salon charte rename failed", exc_info=True)
    return existing


async def ensure_realm_structure(
    guild: discord.Guild,
    kingdom: Any,
    member_ids: list[int],
) -> discord.CategoryChannel:
    """Provision (idempotently) the « Royaume [Nom] » category and salons.

    Adopts the existing category/salons by slug, then re-syncs the
    permission overwrites so member arrivals/departures always apply.
    ``Territoire``/``Alliances`` are readable by every kingdoms_lord;
    the whole category is invisible to everyone else.
    """
    display = realm_category_name(kingdom.name)
    category = _find_realm_category(guild, kingdom.name)
    if category is None:
        category = await guild.create_category(
            display, overwrites=await _realm_overwrites(guild, member_ids)
        )
        logger.info("KINGDOM REALMS: category %s created for %s", display, kingdom.id)
    else:
        # ordre-salons-v1: an adopted legacy « Royaume Nom » category is
        # renamed to the charte display (idempotent — no API call once
        # the name already reads right).
        if str(getattr(category, "name", "")) != display:
            try:
                await category.edit(name=display)
                logger.info("KINGDOM REALMS: category renamed to %s", display)
            except Exception:
                logger.warning("KINGDOM REALMS: category charte rename failed", exc_info=True)
        await _apply_realm_overwrites(guild, category, member_ids)
    lord_role = _member_role(guild, "kingdoms_lord")

    for key, name in REALM_SALONS:
        # ordre-salons-v1: adoption is decoration-blind and renames the
        # live salon to the charte display (never duplicates it).
        existing = await _adopt_realm_salon(guild, category, key, name)
        channel = (
            existing if existing is not None else await guild.create_text_channel(name, category=category)
        )
        if key in PUBLIC_REALM_SALONS and lord_role is not None:
            # same live finding: the lord role must read the history too,
            # not just view the salon
            await channel.set_permissions(
                lord_role, view_channel=True, send_messages=False, read_message_history=True
            )

    # Drasah 19/10 (restructuration-serveur-v1): purge the salons that
    # became ephemeral buttons (or had no wiring) — best effort.
    for _key, _name in REMOVED_REALM_SALONS:
        doomed = next(
            (
                c
                for c in getattr(category, "channels", [])
                if _slug(getattr(c, "name", "")) == _slug(_name)
            ),
            None,
        )
        if doomed is not None:
            try:
                await doomed.delete()
                logger.info("KINGDOM REALMS: removed salon %s", _name)
            except Exception:
                logger.warning("KINGDOM REALMS: salon removal failed (%s)", _name, exc_info=True)

    # ordre-salons-v1 (Drasah 19/10): a freshly created realm category
    # falls to the bottom of the sidebar — re-apply Drasah's order so
    # Admin (and Profils) stay behind the last realm, as decreed.
    try:
        from kingdoms.mods.kingdoms.kingdom_setup import _enforce_category_order

        await _enforce_category_order(guild)
    except Exception:
        logger.warning("KINGDOM REALMS: category order re-apply failed", exc_info=True)

    return category


async def ensure_member_realm_access(
    guild: discord.Guild,
    kingdom: Any,
    member_id: int,
) -> bool:
    """Guarantee one member's view/history overwrites on their realm category.

    Drasah's live incident (2026-10-18): a King who claimed an admin-
    created throne had no access to his own category — the blanket
    ``ensure_all_realm_structures`` re-sync skipped him silently when
    the member lookup failed inside ``_realm_overwrites``. This
    targeted pass runs AFTER the re-sync: it reads the live overwrites
    and grants the missing member overwrite directly, logging loudly
    instead of failing silently.
    """
    category = _find_realm_category(guild, kingdom.name)
    if category is None:
        logger.warning(
            "KINGDOM REALMS: no category %s to guarantee access for %s", kingdom.name, member_id
        )
        return False
    member = guild.get_member(member_id)
    if member is None:
        try:
            member = await guild.fetch_member(member_id)
        except Exception:
            member = None
    if member is None:
        logger.warning(
            "KINGDOM REALMS: member %s unresolvable, access not guaranteed", member_id
        )
        return False
    lookup = getattr(category, "permission_overwrite_for", None)
    existing: Any = None
    if lookup is not None:
        existing = lookup(member)
    else:
        current = getattr(category, "overwrites", None) or {}
        existing = next(
            (
                overwrite
                for target, overwrite in current.items()
                if getattr(target, "id", None) == member.id
            ),
            None,
        )
    if existing is not None and bool(getattr(existing, "view_channel", None)):
        return True
    try:
        await category.set_permissions(member, view_channel=True, read_message_history=True)
        logger.info(
            "KINGDOM REALMS: targeted access granted for %s on %s", member_id, kingdom.name
        )
    except Exception:
        logger.warning(
            "KINGDOM REALMS: targeted access grant failed for %s", member_id, exc_info=True
        )
        return False
    # taverne-resync-v1 (Drasah 30/10): the targeted grant can restore a
    # member ALLOW over a tavern insider's deny — re-pose the denies.
    from kingdoms.mods.kingdoms.tavern_immersion import reapply_tavern_member_denies_safe

    await reapply_tavern_member_denies_safe(guild, [category])
    return True


async def delete_orphan_realm_categories(guild: discord.Guild, kingdoms: Any) -> int:
    """Delete every « Royaume … » category matching no kingdom of the set.

    A season launch wipes the season data but never touches Discord: the
    categories of kingdoms that no longer exist would pile up forever.
    Called after every launch — a category is kept only when its slug
    matches a current kingdom's name, or when it is Gaïa's own declared
    structure (« Royaume Gaïa », kept unconditionally).
    """
    # Gaïa's declared structure (« 🏰 ɢᴀɪᴀ », slugging to « gaia ») must
    # NEVER be swept as an orphan (Drasah's live incident 2026-10-11:
    # the category and its three salons vanished after a launch). It is
    # kept even when the kingdoms list is unavailable. ordre-salons-v1:
    # the keep set covers the charte form AND the legacy « Royaume Gaïa ».
    keep = {_slug(realm_category_name(GAIA_KINGDOM_DISPLAY))}
    keep |= {_slug(f"Royaume {GAIA_KINGDOM_DISPLAY}")}
    keep |= {_slug(realm_category_name(k.name)) for k in kingdoms if not k.is_gaia}
    keep |= {_slug(f"Royaume {k.name}") for k in kingdoms if not k.is_gaia}
    # ordre-salons-v1: the charte realm categories are « 🏰 Nom » — the
    # 🏰 prefix also covers the structure categories: their naked slugs
    # are exempt, gaia is kept above. restructuration-gaia-v1
    # (Drasah 24/10): « kingdoms-gaia » joins the exempt set (the
    # renamed structure category) — gaia stays (mid-migration guild).
    structure_exempt = {"kingdoms", "kingdoms-gaia", "gaia"}
    deleted = 0
    for category in list(getattr(guild, "categories", [])):
        raw_name = str(getattr(category, "name", ""))
        slug = _slug(raw_name)
        is_realm = (
            slug.startswith("royaume-")
            or (raw_name.startswith("🏰 ") and slug not in structure_exempt)
        )
        if not is_realm or slug in keep:
            continue
        try:
            await grant_bot_access_to_category(guild, category)
            await category.delete()
            deleted += 1
            logger.info("KINGDOM REALMS: orphan category %s deleted", slug)
        except Exception:
            logger.warning("KINGDOM REALMS: orphan delete failed", exc_info=True)
    return deleted


async def delete_realm_structure(guild: discord.Guild, kingdom_name: str) -> bool:
    """Delete a refused kingdom's structure (category + salons)."""
    category = _find_realm_category(guild, kingdom_name)
    if category is None:
        return False
    await grant_bot_access_to_category(guild, category)
    for channel in list(getattr(category, "channels", [])):
        try:
            await channel.delete()
        except Exception:
            logger.warning("KINGDOM REALMS: salon delete failed", exc_info=True)
    try:
        await category.delete()
    except Exception:
        logger.warning("KINGDOM REALMS: category delete failed", exc_info=True)
    return True


def _carries_panel_marker(message: Any) -> bool:
    """Return True when a message carries the realms marker (any surface)."""
    return REALMS_PANEL_MARKER in message_text(message)


async def _recent_channel_messages(channel: Any) -> list[Any]:
    """Return the channel's recent messages (mock cache or real history)."""
    return await channel_messages(channel)


async def deploy_realms_panel(
    guild: discord.Guild,
    locale: str,
    kingdoms_service: Any,
) -> bool:
    """(Re)pin the Royaumes panel in its channel; False when absent."""
    channel = next((c for c in guild.text_channels if _slug(c.name) == _slug(REALMS_CHANNEL)), None)
    if channel is None:
        return False
    service = kingdoms_service
    if service is None:
        return False
    kingdoms = await service.kingdoms()
    lords = await service.lords()
    for message in await _recent_channel_messages(channel):
        if _carries_panel_marker(message):
            try:
                await message.delete()
            except Exception:
                logger.warning("KINGDOM REALMS: old panel removal failed", exc_info=True)
    await channel.send(view=build_realms_panel(locale, kingdoms, lords), allowed_mentions=NO_MENTIONS)
    return True


async def ensure_all_realm_structures(guild: discord.Guild, kingdoms_service: Any) -> None:
    """Provision the structure of every APPROVED kingdom (idempotent).

    Called after a season launch (imposed kingdoms are approved up
    front), after an admin creates a kingdom, and at panel deployment
    so a re-install heals any missing category.
    """
    if kingdoms_service is None:
        return
    for kingdom in await kingdoms_service.kingdoms():
        if kingdom.is_gaia or str(kingdom.validation) != "approved":
            continue
        lords = [
            lord
            for lord in await kingdoms_service.lords()
            if lord.kingdom_id == kingdom.id and not lord.left
        ]
        member_ids = [int(lord.id) for lord in lords if str(lord.id).isdigit()]
        try:
            await ensure_realm_structure(guild, kingdom, member_ids)
        except Exception:
            logger.warning("KINGDOM REALMS: structure failed for %s", kingdom.id, exc_info=True)
        try:
            await _announce_realm_alliances(guild, kingdom, kingdoms_service)
        except Exception:
            logger.warning("KINGDOM REALMS: draft announce failed for %s", kingdom.id, exc_info=True)
    # ejection-cleanup-v1 (Drasah 30/10): the departed lords' stale
    # member overwrites never leave on their own — a realm's re-sync is
    # a replace-edit that only runs on launch/approval, and the ghost
    # of an older ejection survives it. The blanket re-sync self-heals
    # them on every seam.
    try:
        await sweep_departed_member_overwrites(guild, kingdoms_service)
    except Exception:
        logger.warning("KINGDOM REALMS: departed member sweep failed", exc_info=True)


async def sweep_departed_member_overwrites(
    guild: discord.Guild | None, kingdoms_service: Any
) -> int:
    """Strip the member overwrites of the DEPARTED lords on realm zones.

    ejection-cleanup-v1 (Drasah 30/10): an ejected lord keeps his
    member allow on the ex-kingdom's category and still reads its
    salons while ruling another kingdom (beta report: AEGIS visible
    from Pytoland). The sweep walks every APPROVED kingdom's category
    and salons, reads the member-level overwrite ids (duck-typed, no
    member resolution — the bot runs without the members intent) and
    strips the ones belonging to no ACTIVE lord of THAT kingdom. The
    tavern insiders are active lords (their member DENIES are safe by
    construction), the bot's own allow and every role overwrite are
    never touched. Idempotent: a converged guild costs zero API call.
    Returns the number of strips.
    """
    if guild is None or kingdoms_service is None:
        return 0
    try:
        kingdoms = await kingdoms_service.kingdoms()
        lords = await kingdoms_service.lords()
    except Exception:
        logger.warning("KINGDOM REALMS: departed sweep state read failed", exc_info=True)
        return 0
    active: dict[str, set[str]] = {}
    for lord in lords:
        if getattr(lord, "left", False):
            continue
        active.setdefault(str(getattr(lord, "kingdom_id", "")), set()).add(str(lord.id))
    from kingdoms.mods.kingdoms.kingdom_persistent import _member_overwrite_ids

    bot_id = getattr(getattr(guild, "me", None), "id", None)
    removed = 0
    for kingdom in kingdoms:
        if getattr(kingdom, "is_gaia", False):
            continue
        if str(getattr(kingdom, "validation", "")) != "approved":
            continue
        category = _find_realm_category(guild, str(getattr(kingdom, "name", "")))
        if category is None:
            continue
        keep = active.get(str(getattr(kingdom, "id", "")), set())
        for channel in (category, *(getattr(category, "channels", []) or [])):
            for overwrite_id in _member_overwrite_ids(channel, bot_id):
                if str(overwrite_id) in keep:
                    continue
                try:
                    await channel.set_permissions(
                        discord.Object(id=overwrite_id, type=discord.User), overwrite=None
                    )
                    removed += 1
                except Exception:
                    logger.warning(
                        "KINGDOM REALMS: departed overwrite strip failed (%s)",
                        overwrite_id,
                        exc_info=True,
                    )
    if removed:
        logger.info(
            "KINGDOM REALMS: ejection-cleanup-v1 — %s stale member overwrite(s) stripped (guild %s)",
            removed,
            guild.id,
        )
    return removed


async def _announce_realm_alliances(
    guild: discord.Guild, kingdom: Any, kingdoms_service: Any
) -> bool:
    """Write one kingdom's Alliances salon content (phase-aware).

    Drasah's 2026-10-12 report: a kingdom born from a lord's proposal
    (free mode → pending) that the admin VALIDATES via the Royaumes
    panel got its salons provisioned but its Alliances message was
    NEVER written — the launch path alone wrote it. Every provisioning
    seam must call this: placeholder during setup, and — since
    alliances-single-message-v1 (Drasah 29/10) — a sweep once started
    (the unified alliances-panel view is the ONE message of the salon).
    """
    phase = "started"
    if kingdoms_service is not None:
        try:
            season = await kingdoms_service.current_season()
        except Exception:
            logger.warning("KINGDOM REALMS: season read failed", exc_info=True)
            season = None
        phase = (
            str(getattr(season, "phase", "started") or "started")
            if season is not None
            else "started"
        )
    if phase == "setup":
        return await announce_alliances_placeholder(guild, kingdom)
    # alliances-single-message-v1 (Drasah 29/10): once launched, ONE
    # message lives in Alliances (the panel) — the retired standalone
    # draft starter and the setup placeholder are swept, nothing new
    # is sent: the panel content deploy is the source of truth.
    return await sweep_kingdom_alliances_messages(guild, kingdom)


DRAFT_STARTER_MARKER = "kingdoms:realm:draft-starter"
ALLIANCES_PENDING_MARKER = "kingdoms:realm:alliances-pending"


async def _split_marked_and_stale(
    channel: Any, marker: str, stale_marker: str
) -> tuple[list[Any], list[Any]]:
    """Split the channel's marked messages from the stale ones.

    vues-silencieuses-v1: a marker conflict (e.g. the setup placeholder
    dying at the draft reveal, or an older duplicate) is returned as the
    stale side; the caller keeps the FIRST marked message.
    """
    marked: list[Any] = []
    stale: list[Any] = []
    for message in await channel_messages(channel):
        text = message_text(message)
        if marker in text:
            marked.append(message)
        elif stale_marker in text:
            stale.append(message)
    return marked, stale


async def _purge_marked_duplicates(marked: list[Any], label: str) -> None:
    """vues-silencieuses-v1: keep the first marked message, delete the rest.

    Legacy duplicates (an older double send, a pre-marker incident) must
    never survive a refresh — the FIRST marked message keeps its pin
    slot, the others are swept.
    """
    for duplicate in marked[1:]:
        try:
            await duplicate.delete()
        except Exception:
            logger.warning("KINGDOM REALMS: %s duplicate purge failed", label, exc_info=True)


async def sweep_standalone_alliances_messages(
    channel: Any, *, include_pending: bool = True
) -> bool:
    """Sweep the retired standalone Alliances messages (idempotent).

    alliances-single-message-v1 (Drasah 29/10): the unified
    alliances-panel view is the ONE live source of the hand (mosaic,
    legend, married holders, mercenary contracts) — the standalone
    « 🎲 Draft starter » message was visually redundant next to it and
    is retired. This sweep deletes any surviving draft-starter message,
    plus the setup-phase pending placeholder when ``include_pending``
    (the season is launched and the panel shows the real hand). Safe on
    every provisioning seam: no message to sweep is the normal case.
    """
    if channel is None:
        return False
    removed = 0
    for message in await channel_messages(channel):
        text = message_text(message)
        if DRAFT_STARTER_MARKER in text or (
            include_pending and ALLIANCES_PENDING_MARKER in text
        ):
            try:
                await message.delete()
                removed += 1
            except Exception:
                logger.warning(
                    "KINGDOM REALMS: standalone alliances sweep failed", exc_info=True
                )
    if removed:
        logger.info(
            "KINGDOM REALMS: swept %s standalone alliances message(s)", removed
        )
    return True


async def sweep_kingdom_alliances_messages(
    guild: discord.Guild, kingdom: Any, *, include_pending: bool = True
) -> bool:
    """Resolve a kingdom's 🤝 salon then sweep its retired messages."""
    category = _find_realm_category(guild, kingdom.name)
    if category is None:
        return False
    channel = next(
        (c for c in getattr(category, "channels", []) if _slug(getattr(c, "name", "")) == _slug("Alliances")),
        None,
    )
    return await sweep_standalone_alliances_messages(channel, include_pending=include_pending)


async def announce_alliances_placeholder(guild: discord.Guild, kingdom: Any) -> bool:
    """Pin the setup-phase Alliances placeholder (idempotent).

    Drasah's phase rule: while the season is in ``setup``, the
    civilization draft must stay secret — the Alliances salon says so
    instead of showing the opening hand. The message is marked and
    edited in place; :func:`sweep_standalone_alliances_messages` deletes
    it at the reveal.
    """
    category = _find_realm_category(guild, kingdom.name)
    if category is None:
        return False
    channel = next(
        (c for c in getattr(category, "channels", []) if _slug(getattr(c, "name", "")) == _slug("Alliances")),
        None,
    )
    if channel is None:
        return False
    content = "\n".join(
        [
            f"# 📜 Alliances — {kingdom.name}",
            "",
            "Les alliances seront révélées au démarrage de la saison :",
            "chaque royaume tirera ses civilisations au sort, sans doublon",
            "entre royaumes.",
            "",
            f"-# {ALLIANCES_PENDING_MARKER}",
        ]
    )
    marked, conflict = await _split_marked_and_stale(channel, ALLIANCES_PENDING_MARKER, DRAFT_STARTER_MARKER)
    if conflict:
        # the draft was already revealed (e.g. the phase moved back or a
        # legacy message): leave it alone, never hide content
        return True
    if marked:
        await _purge_marked_duplicates(marked, "alliances placeholder")
        existing = marked[0]
        if (getattr(existing, "content", "") or "") == content:
            return True
        try:
            await existing.edit(content=content)
            return True
        except Exception:
            logger.warning("KINGDOM REALMS: placeholder refresh failed, resending", exc_info=True)
    await channel.send(content, allowed_mentions=NO_MENTIONS)
    return True
