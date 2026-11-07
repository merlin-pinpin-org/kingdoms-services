"""Entity cards: the shared Components V2 templates for catalog posts.

One builder per surface family — map, pool, faction (civ), and the
generic entity card the mods reuse (kingdoms territories, ladder
objects, ...). Every card follows the same contract:

* a header Section (title, optional thumbnail accessory) — a Section
  holds at most 3 text children (Discord 50035), so the header is a
  single text block by construction;
* property lines (one setting per line) behind no divider;
* a divider, the description, the media, the cross-links, the source
  link, and the id as the small footer — each block behind a divider;
* an action row (the surface's buttons, persistent DynamicItems).

The builders are game/mod-agnostic: they take plain strings and
return items; the callers (the games forums, the mods' forums) own
the data and the buttons. No game or mod imports here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import discord

ACCENT = 0x5865F2


@dataclass(frozen=True)
class EntityCardSpec:
    """One catalog card's content, game/mod-agnostic."""

    title: str
    entity_id: str
    props: tuple[str, ...] = ()
    description: str = ""
    image_url: str = ""
    icon_url: str = ""
    source_url: str = ""
    source_label: str = "Liquipedia"
    creator: str = ""
    links_heading: str = ""
    links: tuple[str, ...] = ()
    action_buttons: tuple[
        Callable[[], discord.ui.Item[discord.ui.LayoutView]]
        | discord.ui.ActionRow[discord.ui.LayoutView]
        | discord.ui.DynamicItem[Any],
        ...
    ] = field(
        default_factory=tuple
    )


def _header(view: discord.ui.LayoutView, spec: EntityCardSpec) -> None:
    """Add the title section (thumbnail = the in-game icon when present)."""
    blocks: list[discord.ui.Item[discord.ui.LayoutView]] = [
        discord.ui.TextDisplay(f"## {spec.title[:1].upper()}{spec.title[1:]}")
    ]
    if spec.icon_url:
        view.add_item(
            discord.ui.Section(*blocks, accessory=discord.ui.Thumbnail(spec.icon_url))
        )
    else:
        view.add_item(discord.ui.Container(*blocks))


def _body(view: discord.ui.LayoutView, spec: EntityCardSpec) -> None:
    """Add the card body: props, description, media, links, source."""
    body: list[discord.ui.Item[discord.ui.LayoutView]] = []
    if spec.props:
        body.append(discord.ui.TextDisplay("\n".join(spec.props)))
    if spec.description:
        body.append(discord.ui.Separator())
        body.append(discord.ui.TextDisplay(spec.description))
    if spec.image_url:
        body.append(discord.ui.Separator())
        body.append(discord.ui.MediaGallery(discord.MediaGalleryItem(spec.image_url)))
    if spec.links:
        body.append(discord.ui.Separator())
        heading = f"**{spec.links_heading}**\n" if spec.links_heading else ""
        body.append(discord.ui.TextDisplay(heading + "\n".join(spec.links)))
    if spec.creator:
        body.append(discord.ui.TextDisplay(f"Créateur : {spec.creator}"))
    if spec.source_url:
        body.append(discord.ui.Separator())
        body.append(discord.ui.TextDisplay(f"Source : [{spec.source_label}]({spec.source_url})"))
    view.add_item(discord.ui.Container(*body, accent_colour=discord.Colour(ACCENT)))


def _actions(
    view: discord.ui.LayoutView,
    buttons: tuple[
        Callable[[], discord.ui.Item[discord.ui.LayoutView]]
        | discord.ui.ActionRow[discord.ui.LayoutView]
        | discord.ui.DynamicItem[Any],
        ...
    ],
) -> None:
    """Add the action rows, in the tuple's order.

    Three shapes ride the same seam: a factory builds a persistent
    item (the posts' persistent buttons), a prebuilt ActionRow lands as
    its own row (the admin menu's lifecycle row), and a DynamicItem
    instance joins the current row (the admin menu's persistent
    buttons — the 2026-11-07 pool-menu crash: they are not callable,
    and treating them as factories killed the menu render).
    """
    if not buttons:
        return
    rows: list[discord.ui.ActionRow[discord.ui.LayoutView]] = []
    current: list[discord.ui.DynamicItem[Any]] = []

    def _flush() -> None:
        if current:
            rows.append(_row_of(current))
            current.clear()

    for button in buttons:
        if isinstance(button, discord.ui.ActionRow):
            _flush()
            rows.append(button)
        elif isinstance(button, discord.ui.DynamicItem):
            current.append(button)
        else:
            _flush()
            rows.append(_row_of([button()]))
    _flush()
    for row in rows:
        view.add_item(row)


def _row_of(
    items: Sequence[discord.ui.DynamicItem[Any] | discord.ui.Item[Any]],
) -> discord.ui.ActionRow[discord.ui.LayoutView]:
    """One action row carrying these dynamic items."""
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


def build_entity_card(spec: EntityCardSpec) -> discord.ui.LayoutView:
    """Build one catalog card from its spec.

    The shared template: header (icon thumbnail) / props / description
    / media / cross-links / source / id footer / action row.
    """
    view = discord.ui.LayoutView(timeout=None)
    _header(view, spec)
    _body(view, spec)
    _actions(view, spec.action_buttons)
    # the id footer is ALWAYS the last line (owner direction 2026-11-06):
    # after the buttons, never above them
    view.add_item(discord.ui.TextDisplay(f"-# `{spec.entity_id}`"))
    return view


def map_card(
    map_id: str,
    name: str,
    props: tuple[str, ...],
    description: str,
    image_url: str,
    icon_url: str,
    source_url: str,
    pool_links: tuple[str, ...],
    buttons: tuple[Callable[[], discord.ui.Item[discord.ui.LayoutView]], ...],
    creator: str = "",
) -> discord.ui.LayoutView:
    """Build a map's forum card on the shared template."""
    return build_entity_card(
        EntityCardSpec(
            title=name,
            entity_id=map_id,
            props=props,
            description=description,
            image_url=image_url,
            icon_url=icon_url,
            source_url=source_url,
            creator=creator,
            links_heading="Map pools",
            links=pool_links,
            action_buttons=buttons,
        )
    )


def pool_card(
    pool_id: str,
    name: str,
    state_line: str,
    description: str,
    member_sections: tuple[tuple[str, str, str], ...],
    buttons: tuple[Callable[[], discord.ui.Item[discord.ui.LayoutView]], ...],
) -> discord.ui.LayoutView:
    """Build a pool's forum card: state header + member sections.

    ``member_sections`` carries ``(name, map_link, image_url)`` per
    map — each becomes a Section (name linking to the map's post)
    with the map image as the accessory.
    """
    view = discord.ui.LayoutView(timeout=None)
    header = f"## {name[:1].upper()}{name[1:]}\n{state_line}"
    if description:
        header += f"\n{description}"
    view.add_item(
        discord.ui.Container(discord.ui.TextDisplay(header), accent_colour=discord.Colour(ACCENT))
    )
    for map_name, link, image in member_sections[:10]:
        title = f"### [{map_name}]({link})" if link else f"### {map_name}"
        if image:
            view.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(title),
                    accessory=discord.ui.Thumbnail(image),
                )
            )
        else:
            view.add_item(discord.ui.TextDisplay(title))
    overflow = [m[0] for m in member_sections[10:]]
    if overflow:
        view.add_item(discord.ui.TextDisplay("\n".join(f"- {n}" for n in overflow)))
    _actions(view, buttons)
    # the id footer is ALWAYS the last line — after the buttons
    view.add_item(discord.ui.TextDisplay(f"-# `{pool_id}`"))
    return view


__all__ = [
    "ACCENT",
    "EntityCardSpec",
    "build_entity_card",
    "map_card",
    "pool_card",
]
