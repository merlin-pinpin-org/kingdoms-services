"""Persistent components: state reconstruction from the custom_id (#122).

A restart wipes every view instance in memory; Discord keeps the
messages. The components here survive restarts because **all their
state rides the custom_id** (``<mod>:<component>:<payload>``) and they
are re-registered at startup (``setup_hook``) through
``bot.add_dynamic_items(...)`` — the §3b state reconstruction
contract of ``docs/architecture/discord.md`` (kingdoms repo).

Rules implemented once here, binding for every mod:

- ``timeout=None``: a persistent component never expires;
- callbacks read **only** the interaction, the reconstructed payload
  and the database — never view attributes capturing a session;
- the custom_id payload stays compact (Discord caps custom ids at
  100 characters — IDs, not text);
- the runtime checks (#55) and the delivery policy (#56) resolve from
  the custom_id at interaction time — a reconstructed item never
  re-applies them itself.

The pagination state of a persistent pager lives in the payload
(``ladder:page:3``), never in memory: the counterpart
:class:`~kingdoms.discord.ui.views.PaginationView` (stateful, for
one in-place editing session) is a different tool for a different
job.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import discord

__all__ = [
    "PersistentPagerButton",
    "register_page_renderer",
    "register_persistent_items",
]

PageRenderer = Callable[[discord.Interaction, int], Awaitable[None]]


class PersistentPagerButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"(?P<mod>[a-z0-9_]+):page:(?P<page>\d+)",
):
    """A restart-proof pager button: the page rides the custom_id.

    One class serves every ``<mod>:page:<n>`` button ever sent; after
    a restart, ``from_custom_id`` reconstructs the item and the click
    renders page ``n`` fetched fresh from the database.
    """

    def __init__(self, mod: str, page: int, *, label: str | None = None) -> None:
        custom_id = f"{mod}:page:{page}"
        super().__init__(
            discord.ui.Button(
                label=label or f"Page {page}",
                style=discord.ButtonStyle.secondary,
                custom_id=custom_id[:100],
            )
        )
        self.mod = mod
        self.page = page

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PersistentPagerButton:
        """Rebuild the item from the wire — the only post-restart path."""
        return cls(match["mod"], int(match["page"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Render the requested page, fresh from the source of truth."""
        renderer = _page_renderers().get(self.mod)
        if renderer is None:
            await interaction.response.send_message("Unknown panel", ephemeral=True)
            return
        await renderer(interaction, self.page)


PageRendererRegistry = dict[str, PageRenderer]

_RENDERERS: PageRendererRegistry = {}


def register_page_renderer(mod: str, renderer: PageRenderer) -> None:
    """Declare how a mod renders one persistent pager page."""
    _RENDERERS[mod] = renderer


def _page_renderers() -> PageRendererRegistry:
    """Return the registered per-mod page renderers."""
    return _RENDERERS


def register_persistent_items(bot: discord.Client) -> None:
    """Re-register every persistent component class on the bot.

    Called from ``setup_hook`` at every startup: a persistent item the
    factory does not re-register is dead UI after the next deploy.
    """
    bot.add_dynamic_items(PersistentPagerButton)
