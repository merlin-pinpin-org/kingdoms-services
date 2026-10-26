"""Kingdoms Taverne immersion (taverne-immersion-v1, Drasah 26/10).

Concept (Drasah, 26/10): the 🍺 taverne salon keeps its place and gains
a pinned FACADE — the outside artwork of the tavern with one
persistent « Entrer dans la Taverne » button. Entering gives the member
the invisible role ``🍺 Dans la Taverne``: the role DENIES view on every
other category (and every un-synced channel) of the server and ALLOWS
view on the private ``🍺 Taverne`` category — so a lord inside the
tavern sees ONLY the Taverne, like a door closing on the court.

The private category carries one salon, ``comptoir``, with the inside
artwork (the barman) and the persistent « Sortir » button that drops
the role and gives the court back.

Mechanics:
- ONE ``set_permissions`` call per target, skipped when already
  converged (lesson fix-reset-429: never a PUT per permission entry);
- idempotent provisioning (role, category, salon, overwrites) safe to
  run at every pass — it also SELF-HEALS a member left inside by a
  crash (the deny overwrites are re-applied, the Sortir button rides a
  persistent message);
- admins and kings immerse the same way (Drasah 26/10: everyone).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

from kingdoms.mods.kingdoms.panel_messages import NO_MENTIONS, channel_messages, message_text
from kingdoms.mods.kingdoms.tavern_art import art_filename, resolve_art

logger = logging.getLogger("kingdoms.tavern_immersion")

FACADE_MARKER = "<!-- kingdoms-tavern-facade -->"
COMPTOIR_MARKER = "<!-- kingdoms-tavern-comptoir -->"

TAVERN_ROLE_NAME = "🍺 Dans la Taverne"
TAVERN_CATEGORY_NAME = "🍺 Taverne"
COMPTOIR_CHANNEL_NAME = "comptoir"

ENTER_CUSTOM_ID = "kingdoms:tavern:enter"
EXIT_CUSTOM_ID = "kingdoms:tavern:exit"

_EPHEMERAL_SECONDS = 10.0
_EPHEMERAL_TASKS: dict[int, asyncio.Task[None]] = {}

_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "facade_title": "🍺 La Taverne",
        "facade_intro": (
            "La nuit tombe sur la vallée — une lumière chaude fuit sous "
            "la porte, avec des rires et des chopes qui se heurtent."
        ),
        "facade_warning": (
            "⚠️ En franchissant le seuil, le reste du royaume disparaît "
            "de ta vue — seule la Taverne restera visible. Le bouton "
            "**Sortir**, à l'intérieur, te rendra la cour."
        ),
        "enter_button": "🚪 Entrer dans la Taverne",
        "entered": (
            "🍺 Tu pousses la porte — la chaleur du comptoir "
            "t'engloutit. Le bouton **Sortir**, dans le salon Comptoir, "
            "te rendra la cour."
        ),
        "comptoir_title": "🪵 Le Comptoir",
        "comptoir_intro": (
            "Le tenancier essuie une chope sans lever les yeux. Ici "
            "coulent les écus et les rumeurs."
        ),
        "exit_button": "🚪 Sortir de la Taverne",
        "exited": "🌙 Tu ressorts de la Taverne — la cour t'accueille de retour.",
        "already": "🍺 Tu es déjà derrière le comptoir.",
        "already_out": "Tu es déjà dehors — la porte est à ta droite.",
        "role_failed": "❌ Le bot n'a pas pu ouvrir/franchir la porte (rôle inaccessible) — préviens un admin.",
    },
    "en": {
        "facade_title": "🍺 The Tavern",
        "facade_intro": (
            "Night falls over the valley — warm light leaks under the "
            "door, with laughter and clanking tankards."
        ),
        "facade_warning": (
            "⚠️ Crossing the threshold hides the rest of the realm from "
            "your sight — only the Tavern stays visible. The **Leave** "
            "button inside gives you the court back."
        ),
        "enter_button": "🚪 Enter the Tavern",
        "entered": (
            "🍺 You push the door — the counter's warmth swallows you. "
            "The **Leave** button in the Bar room gives you the court "
            "back."
        ),
        "comptoir_title": "🪵 The Bar",
        "comptoir_intro": (
            "The barkeep wipes a tankard without looking up. Coins and "
            "rumors flow here."
        ),
        "exit_button": "🚪 Leave the Tavern",
        "exited": "🌙 You step out of the Tavern — the court welcomes you back.",
        "already": "🍺 You are already at the counter.",
        "already_out": "You are already outside — the door is on your right.",
        "role_failed": "❌ The bot could not open/cross the door (role out of reach) — tell an admin.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    return _STRINGS.get(locale, _STRINGS["fr"])


def _slug(value: str) -> str:
    from kingdoms.mods.kingdoms.kingdom_setup import _slug as _kingdom_slug

    return _kingdom_slug(value)


def _find_category(guild: discord.Guild, slug: str) -> Any | None:
    for category in getattr(guild, "categories", []) or []:
        if _slug(str(getattr(category, "name", ""))) == slug:
            return category
    return None


def _find_channel(guild: discord.Guild, category: Any, slug: str) -> Any | None:
    for channel in getattr(guild, "text_channels", []) or []:
        if (
            _slug(str(getattr(channel, "name", ""))) == slug
            and getattr(channel, "category_id", None) == getattr(category, "id", None)
        ):
            return channel
    return None


def find_tavern_role(guild: discord.Guild) -> Any | None:
    """Find the immersion role (read-only lookup)."""
    for role in getattr(guild, "roles", []) or []:
        if str(getattr(role, "name", "")) == TAVERN_ROLE_NAME:
            return role
    return None


async def ensure_tavern_role(guild: discord.Guild) -> Any | None:
    """Find (or create) the invisible immersion role."""
    role = find_tavern_role(guild)
    if role is not None:
        return role
    try:
        role = await guild.create_role(
            name=TAVERN_ROLE_NAME,
            hoist=False,
            mentionable=False,
            permissions=discord.Permissions.none(),
            reason="taverne-immersion-v1: role de la Taverne",
        )
        logger.info("KINGDOMS TAVERN: immersion role created %r", TAVERN_ROLE_NAME)
    except Exception:
        # A role the bot cannot create = the immersion is DEAD — loud.
        logger.error(
            "KINGDOMS TAVERN: cannot create the immersion role (manage_roles missing?)",
            exc_info=True,
        )
        return None
    return role


def _denied_for(target: Any, role: Any) -> bool:
    """True when the target already carries the role's view deny."""
    lookup = getattr(target, "permission_overwrite_for", None)
    overwrite: Any = None
    if lookup is not None:
        overwrite = lookup(role)
    else:  # prod-side overwrites mapping (keyed by Role/Member)
        for key, candidate in (getattr(target, "overwrites", None) or {}).items():
            if getattr(key, "id", None) == getattr(role, "id", None) and candidate is not None:
                overwrite = candidate
                break
    return overwrite is not None and overwrite.view_channel is False


async def _deny_view(target: Any, role: Any, *, reason: str) -> None:
    """Additive single-call role deny (skipped when converged)."""
    if _denied_for(target, role):
        return
    try:
        await target.set_permissions(
            role, overwrite=discord.PermissionOverwrite(view_channel=False), reason=reason
        )
    except Exception:
        logger.warning(
            "KINGDOMS TAVERN: role deny failed on %r",
            str(getattr(target, "name", "?")),
            exc_info=True,
        )


async def ensure_tavern_immersion_structure(guild: discord.Guild, locale: str = "fr") -> bool:
    """Provision the Taverne zone (idempotent) — role, category, salon.

    Returns True when the zone is ready. Best effort per piece: a
    failing deny never blocks the rest, and every piece re-runs at the
    next provisioning pass (self-heal).
    """
    role = await ensure_tavern_role(guild)
    if role is None:
        return False

    category = _find_category(guild, "taverne")
    if category is None:
        try:
            category = await guild.create_category(
                TAVERN_CATEGORY_NAME,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=False, read_message_history=False
                    ),
                    role: discord.PermissionOverwrite(
                        view_channel=True, read_message_history=True, send_messages=True
                    ),
                },
                reason="taverne-immersion-v1: catégorie Taverne",
            )
            logger.info("KINGDOMS TAVERN: category created %r", TAVERN_CATEGORY_NAME)
        except Exception:
            logger.error("KINGDOMS TAVERN: category creation failed", exc_info=True)
            return False
    else:
        # Adopted category: re-apply the three overwrites (additive,
        # one call each, converged-skip keeps the steady state cheap).
        try:
            if not _denied_for(category, guild.default_role):
                await category.set_permissions(
                    guild.default_role,
                    overwrite=discord.PermissionOverwrite(
                        view_channel=False, read_message_history=False
                    ),
                )
            overwrite = discord.PermissionOverwrite(
                view_channel=True, read_message_history=True, send_messages=True
            )
            await category.set_permissions(role, overwrite=overwrite)
        except Exception:
            logger.warning("KINGDOMS TAVERN: category overwrites failed", exc_info=True)
    if guild.me is not None:
        try:
            await category.set_permissions(
                guild.me,
                overwrite=discord.PermissionOverwrite(
                    view_channel=True, send_messages=True, manage_channels=True
                ),
            )
        except Exception:
            logger.warning("KINGDOMS TAVERN: bot allow on category failed", exc_info=True)

    comptoir = _find_channel(guild, category, "comptoir")
    if comptoir is None:
        try:
            comptoir = await guild.create_text_channel(
                COMPTOIR_CHANNEL_NAME,
                category=category,
                reason="taverne-immersion-v1: salon Comptoir",
            )
            logger.info("KINGDOMS TAVERN: comptoir salon created")
        except Exception:
            logger.error("KINGDOMS TAVERN: comptoir salon creation failed", exc_info=True)
            return False

    # The immersion itself: the role denies view on EVERY other
    # category, and on every un-synced (or category-less) channel
    # outside the Taverne — a member inside sees ONLY the Taverne.
    reason = "taverne-immersion-v1: immersion Taverne"
    for other in list(getattr(guild, "categories", []) or []):
        if getattr(other, "id", None) == getattr(category, "id", None):
            continue
        await _deny_view(other, role, reason=reason)
    channels: list[Any] = [
        *(getattr(guild, "text_channels", []) or []),
        *(getattr(guild, "voice_channels", []) or []),
        *(getattr(guild, "forums", []) or []),
    ]
    for channel in channels:
        parent = getattr(channel, "category", None)
        if parent is not None and getattr(parent, "id", None) == getattr(category, "id", None):
            continue
        if parent is not None and getattr(channel, "permissions_synced", True):
            continue  # the category deny covers the synced channels
        await _deny_view(channel, role, reason=reason)

    try:
        await deploy_comptoir_panel(comptoir, locale)
    except Exception:
        logger.warning("KINGDOMS TAVERN: comptoir panel deploy failed", exc_info=True)
    return True


def _art_file(which: str) -> discord.File | None:
    path = resolve_art(which)
    if path is None:
        logger.warning("KINGDOMS TAVERN: %r artwork missing (assets/taverne)", which)
        return None
    return discord.File(path, filename=art_filename(which))


def build_facade_view(locale: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomTavernEnterButton(_strings(locale)["enter_button"]))
    return view


def build_facade_content(locale: str) -> str:
    strings = _strings(locale)
    return (
        f"{FACADE_MARKER}\n"
        f"**{strings['facade_title']}**\n"
        f"{strings['facade_intro']}\n\n{strings['facade_warning']}"
    )


def build_comptoir_view(locale: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomTavernExitButton(_strings(locale)["exit_button"]))
    return view


def build_comptoir_content(locale: str) -> str:
    strings = _strings(locale)
    return (
        f"{COMPTOIR_MARKER}\n**{strings['comptoir_title']}**\n{strings['comptoir_intro']}"
    )


async def _deploy_art_panel(
    channel: Any,
    *,
    marker: str,
    content: str,
    view: discord.ui.View,
    which: str,
) -> bool:
    """Pin (or refresh in place) one artwork panel — the art never
    re-uploads when it is already on the message."""
    try:
        for message in await channel_messages(channel):
            if marker in message_text(message):
                file = None
                if not getattr(message, "attachments", None):
                    file = _art_file(which)
                if file is not None:
                    await message.edit(content=content, view=view, attachments=[file])
                else:
                    await message.edit(content=content, view=view)
                return True
        file = _art_file(which)
        await channel.send(
            content=content,
            view=view,
            files=[file] if file is not None else [],
            allowed_mentions=NO_MENTIONS,
        )
        return True
    except Exception:
        logger.warning("KINGDOMS TAVERN: %r panel deploy failed", which, exc_info=True)
        return False


async def deploy_tavern_facade(channel: Any, locale: str) -> bool:
    """Pin the facade (outside art + Enter button) in the 🍺 taverne salon."""
    return await _deploy_art_panel(
        channel,
        marker=FACADE_MARKER,
        content=build_facade_content(locale),
        view=build_facade_view(locale),
        which="entree",
    )


async def deploy_comptoir_panel(channel: Any, locale: str) -> bool:
    """Pin the inside art + Leave button in the comptoir salon."""
    return await _deploy_art_panel(
        channel,
        marker=COMPTOIR_MARKER,
        content=build_comptoir_content(locale),
        view=build_comptoir_view(locale),
        which="comptoir",
    )


def _arm_ephemeral_timer(interaction: discord.Interaction) -> None:
    """Auto-delete the mover's ephemeral note (Drasah: no pollution)."""
    user_id = getattr(interaction.user, "id", None)
    if user_id is None:
        return
    previous = _EPHEMERAL_TASKS.pop(user_id, None)
    if previous is not None:
        previous.cancel()

    async def _delete() -> None:
        await asyncio.sleep(_EPHEMERAL_SECONDS)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass
        _EPHEMERAL_TASKS.pop(user_id, None)

    _EPHEMERAL_TASKS[user_id] = asyncio.create_task(_delete())


async def _move_member(interaction: discord.Interaction, *, entering: bool) -> None:
    guild = interaction.guild
    locale = str(interaction.locale) if interaction.locale else "fr"
    strings = _strings(locale)
    if guild is None:
        await interaction.response.send_message(strings["role_failed"], ephemeral=True)
        return
    role = await ensure_tavern_role(guild)
    if role is None:
        await interaction.response.send_message(strings["role_failed"], ephemeral=True)
        return
    member = interaction.user
    inside = role in list(getattr(member, "roles", []) or [])
    if entering == inside:
        note = strings["already"] if inside else strings["already_out"]
        await interaction.response.send_message(note, ephemeral=True)
        _arm_ephemeral_timer(interaction)
        return
    try:
        if entering:
            await member.add_roles(role, reason="taverne-immersion-v1: entrée")
        else:
            await member.remove_roles(role, reason="taverne-immersion-v1: sortie")
    except Exception:
        logger.error("KINGDOMS TAVERN: role move failed", exc_info=True)
        await interaction.response.send_message(strings["role_failed"], ephemeral=True)
        return
    await interaction.response.send_message(
        strings["entered"] if entering else strings["exited"], ephemeral=True
    )
    _arm_ephemeral_timer(interaction)


class KingdomTavernEnterButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=ENTER_CUSTOM_ID,
):
    """The restart-proof door-in button of the taverne facade."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=ENTER_CUSTOM_ID,
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: Any,
        /,
    ) -> KingdomTavernEnterButton:
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["enter_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await _move_member(interaction, entering=True)


class KingdomTavernExitButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=EXIT_CUSTOM_ID,
):
    """The restart-proof door-out button of the comptoir."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=EXIT_CUSTOM_ID,
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: Any,
        /,
    ) -> KingdomTavernExitButton:
        locale = str(interaction.locale) if interaction.locale else "fr"
        return cls(_strings(locale)["exit_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await _move_member(interaction, entering=False)
