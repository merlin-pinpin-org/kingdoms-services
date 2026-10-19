"""Views for the visual forum cards: public + admin button rows.

A visual card is an image attachment on a forum post (see
``docs/DEVELOPMENT/visual-cards.md``); Discord components cannot ride
on the same message as the attachment's layout, so the buttons attach
to the post's starter message as a persistent, restart-proof view.

Two rows, two audiences:

- **public row** — what every member may click: link to the entity's
  source, refresh hint.
- **admin row** — behind a single ``admin`` button: the clicker gets
  an *ephemeral* view with the admin actions (regenerate the card,
  re-attach it). The admin actions themselves are permission-gated at
  click time — the hidden row is UX, not the security boundary
  (#55: runtime checks are the boundary, visibility is not).

State reconstruction follows #122: the entity id and kind ride the
custom_id (``<mod>:<component>:<kind>:<payload>``), the items rebuild
via ``from_custom_id`` after a restart.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.visual_card_views")

_NS = "core:vcard"


async def _guard_admin(interaction: discord.Interaction) -> bool:
    """Deny non-admins ephemerally; True when the clicker may proceed."""
    from kingdoms.discord.wiring import guard_admin

    return await guard_admin(interaction)


class CardAdminButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:admin:(?P<kind>[a-z]+):(?P<payload>.+)",
):
    """Public row: the admin drawer toggle (admins only, ephemeral reveal)."""

    def __init__(self, kind: str, payload: str) -> None:
        self.kind = kind
        self.payload = payload
        super().__init__(
            discord.ui.Button(
                label="Admin",
                emoji="⚙️",
                style=discord.ButtonStyle.secondary,
                custom_id=f"{_NS}:admin:{kind}:{payload}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> CardAdminButton:
        """Rebuild from the wire; the entity id rides the custom_id."""
        return cls(match.group("kind"), match.group("payload"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Reveal the ephemeral admin drawer (admins only)."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.send_message(
            view=CardAdminView(self.kind, self.payload),
            ephemeral=True,
        )


class CardRegenerateButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{_NS}:regen:(?P<kind>[a-z]+):(?P<payload>.+)",
):
    """Admin drawer: regenerate the card and re-attach it to the post."""

    def __init__(self, kind: str, payload: str) -> None:
        self.kind = kind
        self.payload = payload
        super().__init__(
            discord.ui.Button(
                label="Régénérer la fiche",
                emoji="🔄",
                style=discord.ButtonStyle.primary,
                custom_id=f"{_NS}:regen:{kind}:{payload}"[:100],
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> CardRegenerateButton:
        """Rebuild from the wire; the entity id rides the custom_id."""
        return cls(match.group("kind"), match.group("payload"))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Regenerate the card and answer with the fresh image."""
        if not await _guard_admin(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            new_path = await regenerate_card(self.kind, self.payload, interaction)
        except Exception:
            logger.warning("vcard: regeneration failed (%s:%s)", self.kind, self.payload, exc_info=True)
            await interaction.followup.send("Régénération impossible (voir les logs).", ephemeral=True)
            return
        if new_path is None:
            await interaction.followup.send("Fiche introuvable.", ephemeral=True)
            return
        await interaction.followup.send(
            content="Fiche régénérée :",
            file=discord.File(new_path, filename=os.path.basename(new_path)),
            ephemeral=True,
        )


class CardAdminView(discord.ui.View):
    """The ephemeral admin drawer: one row of admin actions."""

    def __init__(self, kind: str, payload: str) -> None:
        super().__init__(timeout=None)
        self.add_item(CardRegenerateButton(kind, payload))


def card_view(kind: str, payload: str, source_url: str | None = None) -> discord.ui.View:
    """Build the persistent view shown under a card's post.

    ``kind``/``payload`` identify the entity (``faction`` /
    ``map:aoe2:arabia``); ``source_url`` feeds the public source link.
    The admin drawer stays behind its own toggle.
    """
    view = discord.ui.View(timeout=None)
    if source_url:
        view.add_item(
            discord.ui.Button(
                label="Source",
                emoji="🔗",
                style=discord.ButtonStyle.link,
                url=source_url[:512],
            )
        )
    view.add_item(CardAdminButton(kind, payload))
    return view


async def regenerate_card(kind: str, payload: str, interaction: discord.Interaction) -> str | None:
    """Regenerate one entity's card; returns the new file path.

    Rebuilds through the visual_cards builders so the mapping stays in
    one place. ``interaction`` provides the guild for locale/content.
    """
    from kingdoms.discord.visual_cards import faction_card

    if kind != "faction":
        return None
    from kingdoms.discord.content_posts import entity_post_content

    guild_id = str(interaction.guild_id or "")
    content = await entity_post_content(payload, payload, guild_id, interaction.client)
    card = faction_card(_StubEntity(payload, payload), content, tags=[], facts=[])
    return card.path


class _StubEntity:
    """Minimal entity duck-type for the card builders (id + name)."""

    def __init__(self, entity_id: str, name: str) -> None:
        self.id = entity_id
        self.name = name


def register_visual_card_items(bot: discord.Client) -> None:
    """Register the card views' DynamicItems (called at every startup)."""
    bot.add_dynamic_items(CardAdminButton)
    bot.add_dynamic_items(CardRegenerateButton)
