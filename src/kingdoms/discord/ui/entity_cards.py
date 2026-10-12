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

from collections.abc import Callable
from dataclasses import dataclass, field

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
    links_heading: str = ""
    links: tuple[str, ...] = ()
    action_buttons: tuple[Callable[[], discord.ui.Item[discord.ui.LayoutView]], ...] = field(
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


def _body(view: discord.ui.LayoutView, spec: EntityCardSpec, footer: str) -> None:
    """Add the card body: props, description, media, links, source, id."""
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
    if spec.source_url:
        body.append(discord.ui.Separator())
        body.append(discord.ui.TextDisplay(f"Source : [{spec.source_label}]({spec.source_url})"))
    body.append(discord.ui.TextDisplay(footer))
    view.add_item(discord.ui.Container(*body, accent_colour=discord.Colour(ACCENT)))


def _actions(
    view: discord.ui.LayoutView,
    buttons: tuple[Callable[[], discord.ui.Item[discord.ui.LayoutView]], ...],
) -> None:
    """Add the action row (each factory builds a persistent item)."""
    if not buttons:
        return
    row: discord.ui.ActionRow[discord.ui.LayoutView] = discord.ui.ActionRow()
    for build in buttons:
        row.add_item(build())
    view.add_item(row)


def build_entity_card(spec: EntityCardSpec) -> discord.ui.LayoutView:
    """Build one catalog card from its spec.

    The shared template: header (icon thumbnail) / props / description
    / media / cross-links / source / id footer / action row.
    """
    view = discord.ui.LayoutView(timeout=None)
    _header(view, spec)
    _body(view, spec, f"-# `{spec.entity_id}`")
    _actions(view, spec.action_buttons)
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
    view.add_item(discord.ui.TextDisplay(f"-# `{pool_id}`"))
    _actions(view, buttons)
    return view


__all__ = [
    "ACCENT",
    "EntityCardSpec",
    "build_entity_card",
    "map_card",
    "pool_card",
]
