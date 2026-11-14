"""Jinja card templating: declared balises, obj + helpers, missing-data notices.

Every card template declares its **balises** — the injection points a
developer (human or agent) fills with data. A balise value is a Jinja
template rendered with the model object (``obj``) and the platform
helpers in scope, so a plain string rides as-is and a dynamic value
composes the helpers::

    values = {
        "state": "**\u00c9tat** : \u2705 **Publi\u00e9**",
        "summary": "Mod \u00e0 installer : {{ link.post(obj.guild_id, obj.mod_post_id) }}",
        "deadline": "{{ date.at(obj.deadline) }}",
    }

The rules (owner direction, 2026-11-13):

* a balise the caller does not fill renders the missing-data notice,
  naming EVERY missing key (the dev sees where the gaps are);
* a balise explicitly set to ``None`` is skipped silently — opted out,
  never alerted;
* the helpers cover Discord links (message, post, channel), mentions
  (user, channel, role) and dates (Discord timestamps, relative by
  default, customizable style).

The templates live close to their card family (``MAP_CARD_TEMPLATE``,
``POOL_CARD_TEMPLATE``, ``CIV_CARD_TEMPLATE``); the entity card
consumes the rendered zones through
``templated_entity_card`` — the wrappers (``map_card``/``pool_card``/
``civ_card``) ride it, so every current post is built by this tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NamedTuple

import discord
from jinja2 import Environment

from botforge.discord.ui.entity_cards import EntityCardBuilder

# The balise value's Jinja environment: Discord markdown, no escaping
# (the values are the platform's own data, never user-supplied HTML).
_ENV = Environment(autoescape=False, keep_trailing_newline=False)  # noqa: S701 — Discord markdown, not HTML

ZONE_SUBTITLE = "subtitle"
ZONE_PROPS = "props"
ZONE_BODY = "body"

_MISSING = object()


class LinkHelpers(NamedTuple):
    """The Discord link helpers every balise value can compose."""

    @staticmethod
    def message(guild_id: Any, channel_id: Any, message_id: Any) -> str:
        """Return the canonical link to one message."""
        return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

    @staticmethod
    def post(guild_id: Any, message_id: Any) -> str:
        """Return the forum post's link (the thread's starter message shape)."""
        return f"https://discord.com/channels/{guild_id}/{message_id}/{message_id}"

    @staticmethod
    def channel(channel_id: Any) -> str:
        """Return a channel's browser link."""
        return f"https://discord.com/channels/{channel_id}"


class MentionHelpers(NamedTuple):
    """The Discord mention formatters (client-rendered, locale-free)."""

    @staticmethod
    def user(user_id: Any) -> str:
        """Return the mention of one user."""
        return f"<@{user_id}>"

    @staticmethod
    def channel(channel_id: Any) -> str:
        """Mention one channel (renders as #name)."""
        return f"<#{channel_id}>"

    @staticmethod
    def role(role_id: Any) -> str:
        """Mention one role."""
        return f"<@&{role_id}>"


class DateHelpers(NamedTuple):
    """The Discord timestamp formatters — relative by default."""

    @staticmethod
    def at(epoch: Any, style: str = "R") -> str:
        """Format an instant as a Discord timestamp (R by default)."""
        return f"<t:{int(epoch)}:{style}>"


@dataclass(frozen=True)
class BaliseDecl:
    """One declared injection point: its key, its zone, its contract.

    The ``description`` is the dev's injection doc — a developer
    (human or agent) must know WHAT belongs in a balise without
    reading the renderer.
    """

    key: str
    zone: str
    description: str


@dataclass(frozen=True)
class CardTemplate:
    """One card family's template: the declared balises, in order."""

    name: str
    balises: tuple[BaliseDecl, ...]

    def balise(self, key: str) -> BaliseDecl:
        """Resolve one balise's declaration."""
        for balise in self.balises:
            if balise.key == key:
                return balise
        raise KeyError(f"{self.name}: unknown balise {key!r}")


MAP_CARD_TEMPLATE = CardTemplate(
    name="map",
    balises=(
        BaliseDecl("tags", ZONE_SUBTITLE, "The map's tags line (open, 2 players) — one line."),
        BaliseDecl("pools", ZONE_SUBTITLE, "The pool links — one per line, never inline."),
        BaliseDecl("summary", ZONE_BODY, "The map's description, paragraphs split on double newlines."),
    ),
)

POOL_CARD_TEMPLATE = CardTemplate(
    name="pool",
    balises=(
        BaliseDecl("state", ZONE_PROPS, "The state badge and the member count line."),
        BaliseDecl("summary", ZONE_BODY, "The pool's description."),
    ),
)

CIV_CARD_TEMPLATE = CardTemplate(
    name="civ",
    balises=(
        BaliseDecl("type", ZONE_SUBTITLE, "The civilization's type line."),
        BaliseDecl("summary", ZONE_BODY, "The civ's parsed sections, paragraphs split on double newlines."),
    ),
)


def _notice(locale: str, keys: tuple[str, ...]) -> str:
    """Render the missing-data notice, naming every missing key."""
    from botforge.core.services.i18n import MessageCatalog

    catalog = MessageCatalog("config")
    listed = ", ".join(f"`{key}`" for key in keys)
    return str(catalog.render("card_templates.missing_notice", locale, keys=listed))


def _render_value(value: str, obj: Any, locale: str) -> str:
    """Render one balise value as Jinja with obj and the helpers in scope."""
    context: dict[str, Any] = {
        "obj": obj,
        "link": LinkHelpers(),
        "mention": MentionHelpers(),
        "date": DateHelpers(),
        "locale": locale,
    }
    return _ENV.from_string(value).render(**context)


def render_balises(
    template: CardTemplate,
    obj: Any,
    values: Mapping[str, str | None],
    *,
    locale: str = "en",
) -> dict[str, list[str]]:
    """Render every declared balise into its zone's lines.

    A balise set to ``None`` is skipped silently (opted out); a
    balise missing from ``values`` renders the missing-data notice
    naming EVERY missing key — the dev sees the gaps in place.
    """
    missing = tuple(balise.key for balise in template.balises if balise.key not in values)
    zones: dict[str, list[str]] = {}
    for balise in template.balises:
        if balise.key not in values:
            rendered = _notice(locale, missing) if missing else ""
        else:
            value = values[balise.key]
            if value is None:
                continue
            rendered = _render_value(value, obj, locale)
        if balise.zone == ZONE_BODY:
            # the body keeps its paragraphs: the value's own \n\n are
            # the card's paragraph boundaries — a line split here
            # would turn every bullet into its own separator-divided
            # block (the 2026-11-13 civ regression)
            if rendered.strip():
                zones.setdefault(ZONE_BODY, []).append(rendered)
        else:
            lines = [line for line in rendered.split("\n") if line.strip()]
            if lines:
                zones.setdefault(balise.zone, []).extend(lines)
    return zones


def templated_entity_card(
    template: CardTemplate,
    obj: Any,
    values: Mapping[str, str | None],
    *,
    title: str,
    entity_id: str,
    locale: str = "en",
    view_id: str = "",
    icon_url: str = "",
    member_sections: tuple[tuple[str, str, str], ...] = (),
    buttons: tuple[Any, ...] = (),
    image_url: str = "",
    gallery_images: tuple[str, ...] = (),
    source_url: str = "",
    source_label: str = "Liquipedia",
    creator: str = "",
    creator_url: str = "",
    versions: tuple[dict[str, str], ...] = (),
) -> discord.ui.LayoutView:
    """Build one card from its template's rendered zones.

    The declared balises carry the data zones (subtitle, props,
    body); the structured zones (media, source, credits, versions,
    members, buttons, the two ids) ride the builder — one path for
    every post.
    """
    zones = render_balises(template, obj, values, locale=locale)
    builder = (
        EntityCardBuilder(entity_id, title, locale=locale)
        .view_id(view_id)
        .thumbnail(icon_url)
        .members(*member_sections)
        .buttons(*buttons)
        .media(image_url, *gallery_images)
        .source(source_url, label=source_label)
        .creator(creator, creator_url)
        .versions(*versions)
    )
    subtitle = "\n".join(zones.get(ZONE_SUBTITLE, ()))
    if subtitle:
        builder = builder.subtitle(subtitle)
    props = tuple(zones.get(ZONE_PROPS, ()))
    if props:
        builder = builder.props(*props)
    body = "\n\n".join(zones.get(ZONE_BODY, ()))
    if body:
        builder = builder.description(body)
    return builder.build()


__all__ = [
    "CIV_CARD_TEMPLATE",
    "MAP_CARD_TEMPLATE",
    "POOL_CARD_TEMPLATE",
    "ZONE_BODY",
    "ZONE_PROPS",
    "ZONE_SUBTITLE",
    "BaliseDecl",
    "CardTemplate",
    "DateHelpers",
    "LinkHelpers",
    "MentionHelpers",
    "render_balises",
    "templated_entity_card",
]
