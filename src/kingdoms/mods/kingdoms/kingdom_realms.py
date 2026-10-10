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

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_realms")

REALMS_PANEL_MARKER = "kingdoms:panel:royaumes"
REALMS_CHANNEL = "Royaumes"
REALM_SALONS: tuple[tuple[str, str], ...] = (
    ("salle-du-conseil", "Salle du Conseil"),
    ("patrouille", "Patrouille"),
    ("seigneurs", "Seigneurs"),
    ("le-royaume", "Le-Royaume"),
    ("territoire", "Territoire"),
    ("alliances", "Alliances"),
    ("eglise", "Église"),
    ("pigeon-voyageur", "Pigeon-Voyageur"),
)
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
        "realms_rename_field": "New name",
        "realms_done": "Done.",
        "realms_failed": "Action failed: {error}",
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
        "realms_rename_field": "Nouveau nom",
        "realms_done": "C'est fait.",
        "realms_failed": "L'action a échoué : {error}",
    },
}

STATUS_EMOJI = {"pending": "⏳", "approved": "✅", "refused": "❌"}


def _strings(locale: str) -> dict[str, str]:
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def realm_category_name(kingdom_name: str) -> str:
    """Return the display name of a kingdom's category: « Royaume [Nom] »."""
    return f"Royaume {kingdom_name}"


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
    """One button row per not-yet-approved kingdom (3 buttons, never empty)."""
    buttons = (
        ("approve", strings["realms_approve_button"], discord.ButtonStyle.success),
        ("refuse", strings["realms_refuse_button"], discord.ButtonStyle.danger),
        ("rename", strings["realms_rename_button"], discord.ButtonStyle.secondary),
    )
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    for kingdom in non_gaia:
        if str(kingdom.validation) == "approved":
            continue  # nothing to validate on an approved kingdom
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        kid = str(kingdom.id)[:90]
        for action, label, style in buttons:
            row.add_item(
                discord.ui.Button(
                    label=label[:80],
                    style=style,
                    custom_id=f"kingdoms:realm:{action}:{kid}",
                )
            )
        rows.append(row)
    return rows


def build_realms_panel(locale: str, kingdoms: list[Any], lords: list[Any]) -> discord.ui.LayoutView:
    """Build the Royaumes panel: every kingdom, its state, its people.

    The buttons target pending/refused kingdoms only (an approved one
    has its structure already; renaming stays available everywhere).
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
    """Find a kingdom's category by slug (read-only lookup)."""
    wanted = _slug(realm_category_name(kingdom_name))
    for category in getattr(guild, "categories", []):
        if _slug(getattr(category, "name", "")) == wanted:
            return category  # type: ignore[no-any-return]
    return None


def _member_role(guild: discord.Guild, role_key: str) -> discord.Role | None:
    """Find a mod role (kingdoms_lord / kingdoms_admin) by name."""
    for role in getattr(guild, "roles", []):
        if getattr(role, "name", "") == role_key:
            return role  # type: ignore[no-any-return]
    return None


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
        category = await guild.create_category(display)
        logger.info("KINGDOM REALMS: category %s created for %s", display, kingdom.id)

    everyone = getattr(guild, "default_role", None)
    if everyone is not None:
        await category.set_permissions(everyone, view_channel=False, read_message_history=False)
    admin_role = _member_role(guild, "kingdoms_admin")
    if admin_role is not None:
        await category.set_permissions(admin_role, view_channel=True, manage_channels=True)
    lord_role = _member_role(guild, "kingdoms_lord")
    for member_id in member_ids:
        # the real API only accepts Member/Role overwrites (Object raises)
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except Exception:
                member = None
        if member is None:
            logger.warning("KINGDOM REALMS: member %s unavailable for overwrites", member_id)
            continue
        await category.set_permissions(member, view_channel=True)

    for key, name in REALM_SALONS:
        existing = next(
            (
                c
                for c in getattr(category, "channels", [])
                if _slug(getattr(c, "name", "")) == _slug(name)
            ),
            None,
        )
        channel = (
            existing if existing is not None else await guild.create_text_channel(name, category=category)
        )
        if key in PUBLIC_REALM_SALONS and lord_role is not None:
            await channel.set_permissions(lord_role, view_channel=True, send_messages=False)

    return category


async def delete_realm_structure(guild: discord.Guild, kingdom_name: str) -> bool:
    """Delete a refused kingdom's structure (category + salons)."""
    category = _find_realm_category(guild, kingdom_name)
    if category is None:
        return False
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
    """Return True when a message carries the realms marker (content or components).

    A Components V2 message has an empty ``content``: the marker rides a
    ``TextDisplay`` inside the view, so the components tree is walked.
    """
    if REALMS_PANEL_MARKER in (getattr(message, "content", "") or ""):
        return True
    stack: list[Any] = list(getattr(message, "components", None) or [])
    layout = getattr(message, "layout", None)
    if layout is not None:
        stack.append(layout)
    while stack:
        item = stack.pop()
        if REALMS_PANEL_MARKER in str(getattr(item, "content", "") or ""):
            return True
        stack.extend(getattr(item, "children", None) or [])
    return False


async def _recent_channel_messages(channel: Any) -> list[Any]:
    """Return the channel's recent messages — the mock cache, or real history.

    A real :class:`discord.TextChannel` has no ``.messages`` attribute
    (only the ``history()`` iterator); the in-memory mock keeps one.
    """
    cached = getattr(channel, "messages", None)
    if cached is not None:
        return list(cached)
    return [message async for message in channel.history(limit=100)]


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
    await channel.send(view=build_realms_panel(locale, kingdoms, lords))
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
