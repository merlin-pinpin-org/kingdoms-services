"""The pinned admin menu surface: a fully dynamic Components V2 view (#122).

The pinned menu is a **permanent** message, so its components carry no
live-view state (the \u00a73b reconstruction contract). On top of that, this
module enforces one structural rule learned the hard way:

**Namespace uniqueness \u2014 a custom_id is served by exactly one dispatch
mechanism.** discord.py dispatches a component interaction to the
matching DynamicItems **and** to the live view registered for the
message, so an id shared between a dynamic template and a live closure
runs **both** on every click while the view is alive. The historical
bug: the pinned menu was built by the /admin builders, so its captured
``by="system"`` closures answered the same ``admin:select:locale`` id as
the dynamic handler \u2014 every language change was applied twice and
audited as ``Language set to fr, by <@system>``.

The pinned surface therefore rides its own ``admin:pin:`` namespace:

- ``admin:pin:select:locale`` \u2014 the guild language select;
- ``admin:pin:select:channel`` \u2014 the managed-channel picker;
- ``admin:pin:select:visibility`` \u2014 the logs visibility select;
- ``admin:pin:route:<category>`` \u2014 the routing select, with the
  managed-channel **category** in the payload (the legacy
  ``admin:channels:logs`` id was shared by both categories, so the
  dynamic path always routed to the logs channel);
- ``admin:pin:button:back`` \u2014 the back button.

Every interactive child is a DynamicItem, and a view whose children
are all dynamic items is never registered as a live view: a click has
exactly one dispatch path, restart-proof, with no captured state. The
ephemeral /admin panel keeps its closures on the ``admin:select:*``
ids undisturbed \u2014 no dynamic template matches them anymore.

Click-time guards and services resolve at click time through the
wiring resolver (:func:`~kingdoms.discord.admin_persistent.register_admin_panel_bot`).
"""

from __future__ import annotations

import re
from typing import Any

import discord

from kingdoms.core.services.i18n import MessageCatalog
from kingdoms.core.services.logs import BOT_LOGS_CATEGORY, LogService
from kingdoms.discord.admin import (
    _LOCALE_LABELS,
    LOCALES,
    MANAGED_CHANNELS,
    VISIBILITY_ADMIN_ONLY,
    VISIBILITY_PUBLIC,
    _managed_channel_status,
    _resolve_managed_channel,
    _t,
)
from kingdoms.discord.ui import BLURPLE

__all__ = [
    "PIN_BACK_BUTTON_ID",
    "PIN_CHANNEL_MENU_ID",
    "PIN_LOCALE_SELECT_ID",
    "PIN_ROLES_BUTTON_ID",
    "PIN_VISIBILITY_SELECT_ID",
    "PinBackButton",
    "PinChannelMenu",
    "PinLocaleSelect",
    "PinReadOnlySelect",
    "PinRolesButton",
    "PinRouteSelect",
    "PinVisibilitySelect",
    "build_pin_channel_menu",
    "build_pin_main_menu",
    "pin_route_id",
]

PIN_LOCALE_SELECT_ID = "admin:pin:select:locale"
PIN_CHANNEL_MENU_ID = "admin:pin:select:channel"
PIN_VISIBILITY_SELECT_ID = "admin:pin:select:visibility"
PIN_BACK_BUTTON_ID = "admin:pin:button:back"
PIN_ROLES_BUTTON_ID = "admin:pin:button:roles"
PIN_READ_ONLY_SELECT_ID = "admin:pin:select:read-only"


def pin_route_id(category: str) -> str:
    """Return the routing select id of one managed-channel category."""
    return f"admin:pin:route:{category}"[:100]


def _roles_row(label: str) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Wrap the roles button in its own ActionRow (Discord layout rule)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(PinRolesButton(label))
    return row


def _select_row(item: discord.ui.DynamicItem[Any]) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Wrap one dynamic item in its own ActionRow (Discord layout rule)."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(item)
    return row


class PinLocaleSelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:select:locale",
):
    """The pinned guild-language select (stateless, restart-proof)."""

    def __init__(self, locale: str = "en") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=PIN_LOCALE_SELECT_ID,
                options=[discord.SelectOption(label=_LOCALE_LABELS[loc], value=loc) for loc in LOCALES],
                placeholder=_LOCALE_LABELS.get(locale, locale),
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinLocaleSelect:
        """Rebuild the select from the wire \u2014 the only post-restart path."""
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the guild language through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_locale

        await _handle_locale(interaction)


class PinChannelMenu(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:select:channel",
):
    """The pinned managed-channel picker (opens the channel sub-menu)."""

    def __init__(self, options: list[discord.SelectOption], placeholder: str = "") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=PIN_CHANNEL_MENU_ID,
                options=options,
                placeholder=placeholder or None,
            )
        )

    @classmethod
    def create(
        cls,
        catalog: MessageCatalog | None,
        locale: str,
        status: dict[str, str],
        placeholder: str = "",
    ) -> PinChannelMenu:
        """Build the picker from the guild's current channel status."""
        options = [
            discord.SelectOption(
                label=f"{icon} {_t(catalog, locale, label_key)}",
                value=category,
                description=status.get(category, _t(catalog, locale, "not_provisioned")),
            )
            for category, icon, label_key in MANAGED_CHANNELS
        ]
        return cls(options, placeholder)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinChannelMenu:
        """Rebuild the picker from the wire (generic options; the callback re-renders)."""
        return cls(
            [
                discord.SelectOption(label=f"{icon} {label_key}", value=category)
                for category, icon, label_key in MANAGED_CHANNELS
            ]
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Open the selected channel's sub-menu through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_channel

        await _handle_channel(interaction)


class PinVisibilitySelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:select:visibility",
):
    """The pinned logs-visibility select (public/admin-only)."""

    def __init__(self, options: list[discord.SelectOption], placeholder: str = "") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=PIN_VISIBILITY_SELECT_ID,
                options=options,
                placeholder=placeholder or None,
            )
        )

    @classmethod
    def create(cls, catalog: MessageCatalog | None, locale: str, placeholder: str = "") -> PinVisibilitySelect:
        """Build the visibility options for the guild's locale."""
        options = [
            discord.SelectOption(
                label=f"\U0001f512 {_t(catalog, locale, 'visibility_admin_label')}",
                value=VISIBILITY_ADMIN_ONLY,
                description=_t(catalog, locale, "visibility_admin_hint"),
            ),
            discord.SelectOption(
                label=f"\U0001f513 {_t(catalog, locale, 'visibility_public_label')}",
                value=VISIBILITY_PUBLIC,
                description=_t(catalog, locale, "visibility_public_hint"),
            ),
        ]
        return cls(options, placeholder)

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinVisibilitySelect:
        """Rebuild the select from the wire (generic options; the callback re-renders)."""
        return cls(
            [
                discord.SelectOption(label=VISIBILITY_ADMIN_ONLY, value=VISIBILITY_ADMIN_ONLY),
                discord.SelectOption(label=VISIBILITY_PUBLIC, value=VISIBILITY_PUBLIC),
            ]
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the visibility through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_visibility

        await _handle_visibility(interaction)


class PinRouteSelect(
    discord.ui.DynamicItem[discord.ui.ChannelSelect[Any]],
    template=r"admin:pin:route:(?P<category>[a-z_]+)",
):
    """The pinned channel-routing select; the category rides the payload."""

    def __init__(self, category: str, placeholder: str = "") -> None:
        super().__init__(
            discord.ui.ChannelSelect(
                custom_id=pin_route_id(category),
                placeholder=placeholder or None,
                channel_types=[discord.ChannelType.text],
            )
        )
        self.category = category

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinRouteSelect:
        """Rebuild the routing select from the wire \u2014 the category is the payload."""
        return cls(match["category"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Route the category's channel through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_route

        await _handle_route(interaction, self.category)


class PinBackButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"admin:pin:button:back",
):
    """The pinned back button (channel sub-menu \u2192 main menu)."""

    def __init__(self, label: str = "Back") -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=PIN_BACK_BUTTON_ID,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinBackButton:
        """Rebuild the button from the wire \u2014 the only post-restart path."""
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Return to the main menu through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_back

        await _handle_back(interaction)


class PinReadOnlySelect(
    discord.ui.DynamicItem[discord.ui.Select[Any]],
    template=r"admin:pin:select:read-only",
):
    """The pinned read-only toggle for one channel (default: read-only).

    The managed options cover the core pinned channels plus every
    mod-registered admin surface (the mod hook), addressed by their
    stored channel category.
    """

    def __init__(self, options: list[discord.SelectOption], placeholder: str = "") -> None:
        super().__init__(
            discord.ui.Select(
                custom_id=PIN_READ_ONLY_SELECT_ID,
                options=options or [discord.SelectOption(label="-", value="none")],
                placeholder=placeholder or None,
            )
        )

    @classmethod
    async def read_only_options(cls, guild_id: str = "") -> list[discord.SelectOption]:
        """Build the options: per channel, lock or unlock, with the current state."""
        from kingdoms.discord.pinned_views import get_pinned_read_only

        channels: list[tuple[str, str]] = [
            ("home", "Salon Kingdoms (accueil)"),
            ("admin", "Salon admins du bot"),
        ]
        seen: set[str] = set()
        for spec in spec_registry_resolver().values():
            key = f"mod:{spec.mod}:admin"
            if key in seen:
                continue
            seen.add(key)
            channels.append((key, f"Salon admin {spec.mod}"))
        options: list[discord.SelectOption] = []
        for key, label in channels[:12]:
            locked = True
            if guild_id:
                try:
                    locked = await get_pinned_read_only(guild_id, key)
                except Exception:
                    locked = True
            state = "\U0001f512 lecture seule" if locked else "\u270f\ufe0f messages ouverts"
            options.append(
                discord.SelectOption(label=f"Verrouiller {label}", value=key, description=f"actuel : {state}")
            )
            options.append(
                discord.SelectOption(label=f"Ouvrir {label}", value=f"{key}:open", description=f"actuel : {state}")
            )
        return options[:25]

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinReadOnlySelect:
        """Rebuild the select from the wire (state-aware options)."""
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        return cls(await cls.read_only_options(guild_id))

    async def callback(self, interaction: discord.Interaction) -> None:
        """Apply the read-only intent through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_read_only

        await _handle_read_only(interaction)


_CLIENT_REF: list[discord.Client] = []


def set_panel_client(client: discord.Client) -> None:
    """Hold the running client (the mod-spec registry lives on it)."""
    _CLIENT_REF.clear()
    _CLIENT_REF.append(client)


def spec_registry_resolver() -> dict[str, Any]:
    """Resolve the registered mod specs from the running client."""
    from kingdoms.discord.mod_admin_channels import spec_registry

    if not _CLIENT_REF:
        return {}
    return spec_registry(_CLIENT_REF[0])


class PinRolesButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"admin:pin:button:roles",
):
    """The pinned roles section opener (core roles + mod-declared roles)."""

    def __init__(self, label: str = "Rôles") -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary,
                custom_id=PIN_ROLES_BUTTON_ID,
            )
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> PinRolesButton:
        """Rebuild the button from the wire."""
        del interaction, item, match
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        """Show the roles view through the persistent handler."""
        from kingdoms.discord.admin_persistent import _handle_roles

        await _handle_roles(interaction)


def _access_request_row(guild_id: str) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """Build the access-request row (every guild admin may request)."""
    from kingdoms.discord.guild_access_request import GuildAccessRequestButton

    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    row.add_item(GuildAccessRequestButton(guild_id))
    return row


async def build_pin_main_menu(
    logs_service: LogService,
    guild_id: str,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
    admin_channel_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the pinned main menu: fully dynamic, no captured state."""
    from kingdoms.discord.admin_panel_mods import (
        PinModRouteSelect,
    )

    status = await _managed_channel_status(logs_service, guild_id, admin_channel_service)
    view = discord.ui.LayoutView(timeout=None)
    container_blocks = [
        discord.ui.TextDisplay(f"# \u2699\ufe0f {_t(catalog, locale, 'title')}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"## \ud83c\udf0d {_t(catalog, locale, 'language')}"),
        discord.ui.TextDisplay(_t(catalog, locale, "language_hint", value=_LOCALE_LABELS.get(locale, locale))),
        _select_row(PinLocaleSelect(locale)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"## \ud83d\udccb {_t(catalog, locale, 'channels')}"),
        _select_row(
            PinChannelMenu.create(
                catalog,
                locale,
                status,
                placeholder=_t(catalog, locale, "channels_placeholder"),
            )
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            "## \U0001f512 Lecture seule\n"
            "Les salons \u00e9pingl\u00e9s sont en lecture seule par d\u00e9faut : personne ne peut y \u00e9crire, "
            "seules les vues du bot s'y affichent. Choisis un salon pour le verrouiller ou l'ouvrir aux messages."
        ),
        _select_row(PinReadOnlySelect(await PinReadOnlySelect.read_only_options(guild_id))),
        discord.ui.Separator(),
        discord.ui.TextDisplay("## \U0001f9e9 R\u00f4les"),
        _roles_row(_t(catalog, locale, "roles_button")),
        discord.ui.Separator(),
        discord.ui.TextDisplay(
            "## \U0001f513 Acc\u00e8s games/mods\n"
            "Rien n'est actif par d\u00e9faut : la guilde doit demander l'acc\u00e8s aux "
            "games et mods ; un bot admin l'approuve depuis ses DMs."
        ),
        _access_request_row(guild_id),
    ]
    from kingdoms.discord.admin_panel_mods import registered_admin_core_sections, registered_admin_game_sections

    if registered_admin_core_sections():
        container_blocks.append(discord.ui.Separator())
        container_blocks.append(discord.ui.TextDisplay("## \ud83c\udfae Jeux"))
        container_blocks.append(
            _select_row(
                PinModRouteSelect.create(
                    scope="games",
                    placeholder="Gerer les jeux...",
                )
            )
        )
    if registered_admin_game_sections():
        container_blocks.append(discord.ui.Separator())
        container_blocks.append(discord.ui.TextDisplay("## \ud83d\udd27 Mods"))
        container_blocks.append(
            _select_row(
                PinModRouteSelect.create(
                    placeholder=_t(catalog, locale, "mods_placeholder"),
                )
            )
        )
    view.add_item(
        discord.ui.Container(
            *container_blocks,
            accent_colour=discord.Colour(BLURPLE),
        )
    )
    return view


async def build_pin_channel_menu(
    logs_service: LogService,
    guild_id: str,
    category: str,
    catalog: MessageCatalog | None = None,
    locale: str = "en",
    admin_channel_service: Any = None,
) -> discord.ui.LayoutView:
    """Build the pinned channel sub-menu: fully dynamic, category on the wire."""
    entry = next((e for e in MANAGED_CHANNELS if e[0] == category), None)
    if entry is None:
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.Container(discord.ui.TextDisplay(f"Unknown channel category: `{category}`.")))
        return view
    _, icon, label_key = entry
    label = _t(catalog, locale, label_key)
    channel_id = await _resolve_managed_channel(logs_service, guild_id, category, admin_channel_service)
    status = f"<#{channel_id}>" if channel_id else _t(catalog, locale, "not_provisioned")

    blocks: list[Any] = [
        discord.ui.TextDisplay(f"# {icon} Kingdoms \u2014 {label}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"{_t(catalog, locale, 'channel')}: {status}"),
    ]
    if category == BOT_LOGS_CATEGORY:
        policy = await logs_service.get_access_policy(guild_id)
        visibility = str((policy or {}).get("default", VISIBILITY_ADMIN_ONLY))
        visibility_label = (
            _t(catalog, locale, "visibility_public")
            if visibility == VISIBILITY_PUBLIC
            else _t(catalog, locale, "visibility_admin_only")
        )
        blocks.append(discord.ui.TextDisplay(f"{_t(catalog, locale, 'visibility')}: {visibility_label}"))
        blocks.append(discord.ui.Separator())
        blocks.append(_select_row(PinRouteSelect(category, placeholder=_t(catalog, locale, "route_placeholder"))))
        blocks.append(
            _select_row(
                PinVisibilitySelect.create(
                    catalog,
                    locale,
                    placeholder=_t(catalog, locale, "visibility_placeholder"),
                )
            )
        )
    else:
        blocks.append(discord.ui.Separator())
        blocks.append(discord.ui.TextDisplay(_t(catalog, locale, "admin_channel_note")))
        blocks.append(_select_row(PinRouteSelect(category, placeholder=_t(catalog, locale, "route_placeholder"))))
    blocks.append(discord.ui.Separator())
    blocks.append(_select_row(PinBackButton(_t(catalog, locale, "back"))))
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(*blocks, accent_colour=discord.Colour(BLURPLE)))
    return view
