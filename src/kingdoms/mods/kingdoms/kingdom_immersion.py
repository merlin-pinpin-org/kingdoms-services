"""immersion-v1 (Drasah 20/10): the Outgame/Ingame divide.

The Discord reads like the game: once a member holds a Kingdoms role
(lord, king or admin), the out-of-game zones — the hand-made **Beta**
category and **Conscription** — are hidden from him. The immersion is
the DEFAULT; the member can opt back in at any time through the
« ⚙️ Paramètres utilisateur » button of his realm's pinned sheet, which
grants a MEMBER-level allow that overrides the role deny (both zones
visible at the same time as the game — Drasah: « voir le ingame et le
outgame en même temps »).

The role deny is re-applied at every provisioning pass (idempotent);
the member-level allows are NEVER touched by the passes — the Discord
overwrite itself is the persisted preference, no database needed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import discord

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_immersion")

IMMERSION_CATEGORY_SLUGS: tuple[str, ...] = ("beta", "conscription")
"""The out-of-game categories hidden from in-game members (Drasah's
immersion rule — Beta is hand-made, the pass just skips it when the
slug matches nothing)."""

_IMMERSION_ROLE_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("kingdoms_lord", "Kingdoms Lord"),
    ("kingdoms_king", "Kingdoms King"),
    ("kingdoms_admin", "Kingdoms Admin"),
)


def _find_category_by_slug(guild: discord.Guild, slug: str) -> Any | None:
    """Find a category by decoration-blind slug (read-only lookup)."""
    for category in getattr(guild, "categories", []) or []:
        if _slug(getattr(category, "name", "")) == slug:
            return category
    return None


async def apply_immersion_gating(guild: discord.Guild) -> None:
    """Hide the out-of-game categories from in-game roles (idempotent).

    immersion-v1: the deny rides the ROLE (lord/king/admin alike —
    Drasah: « chacun pourra faire ce qu'il a envie »), so the immersion
    is the default for every accepted member. Member-level allows are
    never touched here — an opted-in member keeps his exception.
    """
    roles = list(getattr(guild, "roles", []) or [])
    for slug in IMMERSION_CATEGORY_SLUGS:
        category = _find_category_by_slug(guild, slug)
        if category is None:
            continue
        for candidates in _IMMERSION_ROLE_CANDIDATES:
            role = next((r for r in roles if str(getattr(r, "name", "")) in candidates), None)
            if role is None:
                continue
            try:
                await category.set_permissions(
                    role, overwrite=discord.PermissionOverwrite(view_channel=False)
                )
            except Exception:
                logger.warning("KINGDOM IMMERSION: %s deny failed for %s", slug, candidates, exc_info=True)


async def set_outgame_visibility(guild: discord.Guild, member: Any, visible: bool) -> bool:
    """Set one member's out-of-game visibility (True when at least one zone moved).

    The member-level allow overrides the role deny — the two zones come
    back AT THE SAME TIME as the game (both states live side by side).
    ``visible=False`` clears the overwrite, falling back to the role
    deny (the immersion default).
    """
    moved = False
    for slug in IMMERSION_CATEGORY_SLUGS:
        category = _find_category_by_slug(guild, slug)
        if category is None:
            continue
        try:
            await category.set_permissions(
                member,
                overwrite=(
                    discord.PermissionOverwrite(view_channel=True, read_message_history=True)
                    if visible
                    else None
                ),
            )
            moved = True
        except Exception:
            logger.warning("KINGDOM IMMERSION: %s member toggle failed", slug, exc_info=True)
    return moved


def outgame_visible(guild: discord.Guild, member: Any) -> bool:
    """Return True when the member carries an explicit out-of-game allow."""
    category = _find_category_by_slug(guild, IMMERSION_CATEGORY_SLUGS[0])
    if category is None:
        return False
    overwrite = getattr(category, "permission_overwrite_for", None)
    if overwrite is None:
        return False
    recorded = overwrite(member)
    return bool(recorded is not None and getattr(recorded, "view_channel", False))


async def clear_outgame_allow_safe(guild: discord.Guild, user_id: str | int) -> None:
    """Clear one member's out-of-game allow (best effort, idempotent).

    validation-immersion-v1 (Drasah 28/10, live report Rakussy): a
    candidate who opted into « tout visible » BEFORE his approval keeps
    a MEMBER-level allow that overrides the role deny — he still sees
    « Postuler » after joining his kingdom. Called at approval: the
    freshly-validated player falls back to the immersion default.
    """
    if guild is None or not str(user_id).isdigit():
        return
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            return
    if member is None:
        return
    await set_outgame_visibility(guild, member, False)


# ---------------------------------------------------------------- strings


def _strings(locale: str) -> dict[str, str]:
    """Return the FR/EN immersion strings."""
    if str(locale).lower().startswith("fr"):
        return {
            "settings_button": "⚙️ Paramètres utilisateur",
            "settings_footer": "-# ⚙️ Réglages hors-jeu — ceci ne fait pas partie du jeu.",
            "user_settings_title": "⚙️ Paramètres utilisateur",
            "user_settings_blurb": (
                "L'immersion masque les salons hors-jeu (Beta, Conscription). "
                "Tu peux les afficher **en même temps** que le jeu, ou revenir à "
                "l'immersion — ton choix, rien n'est imposé."
            ),
            "status_hidden": "Statut : **Immersion** — le hors-jeu est masqué.",
            "status_visible": "Statut : **Tout visible** — le hors-jeu est affiché.",
            "show_button": "👁️ Voir aussi le hors-jeu",
            "hide_button": "Immersion (masquer le hors-jeu)",
            "now_visible": "✅ Le hors-jeu est visible, en même temps que le jeu.",
            "now_hidden": "✅ Le hors-jeu est masqué — retour à l'immersion.",
            "not_a_member": "Ces réglages ne vivent que sur le serveur.",
        }
    return {
        "settings_button": "⚙️ User settings",
        "settings_footer": "-# ⚙️ Out-of-game settings — this is not part of the game.",
        "user_settings_title": "⚙️ User settings",
        "user_settings_blurb": (
            "Immersion hides the out-of-game channels (Beta, Conscription). "
            "You can show them **at the same time** as the game, or go back to "
            "immersion — your choice, nothing is forced."
        ),
        "status_hidden": "Status: **Immersion** — out-of-game is hidden.",
        "status_visible": "Status: **Everything visible** — out-of-game is shown.",
        "show_button": "👁️ Show the out-of-game too",
        "hide_button": "Immersion (hide the out-of-game)",
        "now_visible": "✅ The out-of-game is visible, alongside the game.",
        "now_hidden": "✅ The out-of-game is hidden — back to immersion.",
        "not_a_member": "These settings only live on the server.",
    }


def _locale(interaction: discord.Interaction) -> str:
    return str(getattr(interaction, "locale", "") or "")


# ---------------------------------------------------------------- the flow


def _settings_view(strings: dict[str, str], visible: bool) -> discord.ui.LayoutView:
    """Build the ephemeral user-settings view (the immersion toggle)."""
    status = strings["status_visible"] if visible else strings["status_hidden"]
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.TextDisplay(f"# {strings['user_settings_title']}"))
    view.add_item(discord.ui.TextDisplay(strings["user_settings_blurb"]))
    view.add_item(discord.ui.TextDisplay(status))
    view.add_item(
        discord.ui.ActionRow(
            KingdomOutgameShowButton(strings["show_button"]),
            KingdomOutgameHideButton(strings["hide_button"]),
        )
    )
    return view


class KingdomUserSettingsButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:user:settings:open",
):
    """The restart-proof « ⚙️ Paramètres utilisateur » button (out-of-game footer)."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:user:settings:open",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomUserSettingsButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["settings_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the ephemeral user-settings view (the immersion toggle)."""
        strings = _strings(_locale(interaction))
        guild = interaction.guild
        member = getattr(interaction, "user", None)
        if guild is None or member is None or not isinstance(member, discord.abc.User) or member.id is None:
            await interaction.response.send_message(strings["not_a_member"], ephemeral=True)
            return
        visible = outgame_visible(guild, member)
        await interaction.response.send_message(
            view=_settings_view(strings, visible), ephemeral=True
        )


class KingdomOutgameShowButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:user:outgame:show",
):
    """Grant the member-level allow — the out-of-game comes back."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:user:outgame:show",
                style=discord.ButtonStyle.secondary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomOutgameShowButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["show_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Grant the out-of-game visibility — both zones at the same time."""
        strings = _strings(_locale(interaction))
        guild = interaction.guild
        member = getattr(interaction, "user", None)
        if guild is None or member is None:
            await interaction.response.send_message(strings["not_a_member"], ephemeral=True)
            return
        await set_outgame_visibility(guild, member, True)
        await interaction.response.edit_message(
            view=_settings_view(strings, True), content=strings["now_visible"]
        )


class KingdomOutgameHideButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:user:outgame:hide",
):
    """Clear the member-level allow — back to the immersion default."""

    def __init__(self, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id="kingdoms:user:outgame:hide",
                style=discord.ButtonStyle.primary,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomOutgameHideButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        return cls(_strings(_locale(interaction))["hide_button"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Clear the out-of-game visibility — back to the immersion default."""
        strings = _strings(_locale(interaction))
        guild = interaction.guild
        member = getattr(interaction, "user", None)
        if guild is None or member is None:
            await interaction.response.send_message(strings["not_a_member"], ephemeral=True)
            return
        await set_outgame_visibility(guild, member, False)
        await interaction.response.edit_message(
            view=_settings_view(strings, False), content=strings["now_hidden"]
        )
