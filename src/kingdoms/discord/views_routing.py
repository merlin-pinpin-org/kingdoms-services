"""The view routing: declared view ids -> the handlers that open them.

Two wire conventions across every menu, both driven by the
registry (``config/views.yaml``):

- ``view:open:<view id>`` — navigation: the router opens the wired
  handler (ephemeral); an undeclared id answers with the
  unknown-view reply, never a silent failure;
- ``view:act:<view id>:<action id>`` — behavior: the router runs the
  action handler wired at boot (``wire_action``); an unwired action
  answers with the not-wired reply (and renders disabled).

Adding a view or an action means declaring it in the YAML and wiring
its handler — the menus pick both up mechanically.

Adding a view means: declaring it (``views_declared``), documenting
it (docs/DEVELOPMENT/VIEWS.md), and wiring its handler here — the
menus pick it up mechanically.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from kingdoms.discord.views_registry import get_view

ROUTER_NS = "view:open"
BUTTON_TEMPLATE = re.compile(rf"{ROUTER_NS}:(?P<view_id>[a-z0-9:_-]+)")

Handler = Callable[[discord.Interaction], Awaitable[None]]


async def open_declared_view(interaction: discord.Interaction, view_id: str) -> None:
    """Open a declared view by id (ephemeral), unknown ids answered.

    A wired panel runs its handler; a menu (no handler — menus only
    navigate) builds from the registry and answers directly. Only
    an id that is neither wired nor declared answers "unknown view".
    """
    handler = _HANDLERS.get(view_id)
    if handler is not None:
        await handler(interaction)
        return
    if get_view(view_id) is not None:
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        builder = build_declared_menu(interaction.client, view_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(view=await builder(guild_id), ephemeral=True)
        return
    from kingdoms.discord.commands_i18n import reply

    await interaction.response.send_message(await reply(interaction, "unknown_view"), ephemeral=True)


def wire_view(view_id: str, handler: Handler) -> None:
    """Wire one declared view's handler (idempotent, last wins)."""
    _HANDLERS[view_id] = handler


def wired_views() -> tuple[str, ...]:
    """Return the wired view ids (diagnostics, tests)."""
    return tuple(sorted(_HANDLERS))


_HANDLERS: dict[str, Handler] = {}
_ACTION_HANDLERS: dict[str, Handler] = {}
ACT_NAMESPACE = "view:act"


def wire_action(view_id: str, action_id: str, handler: Handler) -> None:
    """Wire one declared action's handler (idempotent, last wins)."""
    _ACTION_HANDLERS[f"{view_id}:{action_id}"] = handler


def wired_actions() -> tuple[str, ...]:
    """Return the wired action keys (diagnostics, tests)."""
    return tuple(sorted(_ACTION_HANDLERS))


def action_is_wired(view_id: str, action_id: str) -> bool:
    """Whether a declared action has a handler."""
    return f"{view_id}:{action_id}" in _ACTION_HANDLERS


async def run_declared_action(interaction: discord.Interaction, view_id: str, action_id: str) -> None:
    """Run a declared action's handler; unwired actions answered."""
    handler = _ACTION_HANDLERS.get(f"{view_id}:{action_id}")
    if handler is None:
        from kingdoms.discord.commands_i18n import reply

        await interaction.response.send_message(
            await reply(interaction, "not_configured"), ephemeral=True
        )
        return
    await handler(interaction)


class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{ACT_NAMESPACE}:(?P<view_id>[a-z0-9:_-]+):(?P<action_id>[a-z0-9_-]+)",
):
    """One declared action button: runs its wired handler at click time.

    An action declared but not wired renders disabled (never dead):
    the builder checks ``action_is_wired`` before enabling it.
    """

    def __init__(self, view_id: str, action_id: str, label: str, emoji: str = "", disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                custom_id=f"{ACT_NAMESPACE}:{view_id}:{action_id}"[:100],
                label=label,
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
            )
        )
        self.view_id = view_id
        self.action_id = action_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ActionButton:
        """Rebuild the action button from the wire."""
        del interaction
        button = item if isinstance(item, discord.ui.Button) else None
        label = (button.label if button is not None else None) or "?"
        emoji = button.emoji.name if button is not None and isinstance(button.emoji, discord.PartialEmoji) else ""
        return cls(match["view_id"], match["action_id"], label, emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Run the declared action (guarded actions guard themselves)."""
        await run_declared_action(interaction, self.view_id, self.action_id)


class ViewOpenButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=rf"{ROUTER_NS}:(?P<view_id>[a-z0-9:_-]+)",
):
    """One menu button: opens its declared target view at click time.

    Admin targets render with the padlock emoji and the most discreet
    style Discord offers (secondary, same as every menu button — the
    padlock alone carries the admin marker).
    """

    def __init__(self, view_id: str, label: str, emoji: str = "", disabled: bool = False) -> None:
        from kingdoms.discord.views_registry import ADMIN_EMOJI

        super().__init__(
            discord.ui.Button(
                custom_id=f"{ROUTER_NS}:{view_id}"[:100],
                label=label,
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                style=discord.ButtonStyle.secondary,
                disabled=disabled,
            )
        )
        self.view_id = view_id
        self._admin_emoji = ADMIN_EMOJI

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> ViewOpenButton:
        """Rebuild the button from the wire (label kept from the item)."""
        del interaction
        button = item if isinstance(item, discord.ui.Button) else None
        label = (button.label if button is not None else None) or "?"
        emoji = button.emoji.name if button is not None and isinstance(button.emoji, discord.PartialEmoji) else ""
        return cls(match["view_id"], label, emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the declared target view (guarded views guard themselves)."""
        await open_declared_view(interaction, self.view_id)


def register_view_router(bot: discord.Client) -> None:
    """Register the router's DynamicItems (every startup)."""
    bot.add_dynamic_items(ViewOpenButton)
    bot.add_dynamic_items(ActionButton)


__all__ = [
    "ACT_NAMESPACE",
    "BUTTON_TEMPLATE",
    "ActionButton",
    "ViewOpenButton",
    "action_is_wired",
    "open_declared_view",
    "register_view_router",
    "run_declared_action",
    "wire_action",
    "wire_view",
    "wired_actions",
    "wired_views",
]


def menu_buttons(decl: Any, catalog: Any, locale: str) -> list[discord.ui.Button[Any]]:
    """Build a declared view's buttons (labels i18n, admin/sync emojis).

    A menu button opens another view (``view:open:<target>``); a sync
    button carries the ensure custom id; an action button (a menu's
    declared action, e.g. "add a map") is not routable and therefore
    disabled unless its real handler wired it — kept here for the
    layout so the doc's menus render exactly as declared.
    """
    from kingdoms.discord.views_registry import default_button_emoji

    out: list[discord.ui.Button[Any]] = []
    for button in decl.buttons:
        emoji = button.emoji or default_button_emoji(button)
        item: Any
        if button.target:
            item = ViewOpenButton(button.target, _label(catalog, button.label_key, locale), emoji, button.disabled)
        elif button.sync:
            from kingdoms.discord.admin_tree import TreeEnsureButton

            # the sync re-provisions the declared tree scope (the folder),
            # never the pin channel — the pin scope ends with "/admin" or
            # "/general", which the tree does not declare
            scope = decl.scope.rsplit("/", 1)[0] if decl.scope and "/" in decl.scope else (decl.scope or "bot")
            item = TreeEnsureButton(
                scope, _label(catalog, button.label_key, locale) or "Sync now"
            )
        elif button.action:
            item = ActionButton(
                decl.id,
                button.action,
                _label(catalog, button.label_key, locale),
                emoji,
                disabled=button.disabled or not action_is_wired(decl.id, button.action),
            )
        else:
            item = discord.ui.Button(
                label=_label(catalog, button.label_key, locale),
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )
        out.append(item.item if isinstance(item, ViewOpenButton) else item)
    return out


def _label(catalog: Any, key: str, locale: str, fallback: str = "?") -> str:
    if catalog is None or not key:
        return fallback
    rendered = catalog.render(key, locale)
    return rendered if rendered != key else fallback


def build_declared_menu(bot: Any, view_id: str) -> Any:
    """Build a declared menu's layout: title, description, buttons, footer.

    The single menu builder: every declared menu renders through it,
    so the layout rules (i18n, admin/sync emojis, view id footer,
    channel notice) live in one place and every menu follows.
    """
    import discord


    decl = get_view(view_id)
    if decl is None:
        raise KeyError(f"undeclared view {view_id!r} — declare it in views_declared")

    async def _build(guild_id: str) -> discord.ui.LayoutView:

        from kingdoms.discord.scope_pins import _catalog, _locale_of, _tr, _tree_notice

        catalog = _catalog(bot)
        locale = await _locale_of(bot, guild_id)
        prefix = "ui.views." + view_id.replace(":", ".")
        title = _tr(catalog, f"{prefix}.title", locale, view_id)
        description = _tr(catalog, f"{prefix}.description", locale)
        view = discord.ui.LayoutView(timeout=None)
        header = f"# {title}"
        if getattr(decl, "title_emoji", ""):
            header = f"{header} {decl.title_emoji}"
        blocks: list[Any] = [discord.ui.TextDisplay(header)]
        if description:
            blocks.append(discord.ui.TextDisplay(description))
        # the pin scope is the channel ("games/general"); the tree scope is
        # the folder ("games") — the notice resolves the declared tree scope
        tree_scope = decl.scope.split("/general")[0] if decl.scope else ""
        notice = await _tree_notice(guild_id, tree_scope, catalog, locale) if tree_scope else ""
        if notice:
            blocks.append(discord.ui.TextDisplay(notice))
        view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(0x5865F2)))
        row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
        for button in menu_buttons(decl, catalog, locale):
            row.add_item(button)
        view.add_item(row)
        from kingdoms.core.ids import footer

        view.add_item(discord.ui.TextDisplay(footer(view_id)))
        return view

    return _build
