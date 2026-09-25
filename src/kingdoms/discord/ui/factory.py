"""Discord UI SDK: declarative builders for embeds and Components V2 layouts.

One entry point — :func:`build` — hiding the discord.py machinery
(ADR-0009 dual system: embeds for light informational output,
Components V2 for rich structured UI). The SDK is the **only** way
features build UI: no feature touches ``discord.ui`` classes
directly, so the layout rules below are enforced in one place.

Briques (``kingdoms.discord.ui.factory``):

- :class:`Text` — a text block (accepts full markdown).
- :class`Button` — a link button (label + URL); interactive buttons
  with callbacks are wired through :class:`ButtonRef` + view classes.
- :class:`Section` — text blocks side by side with an accessory
  (a link button or a thumbnail).
- :class:`Row` — a row of link buttons.
- :class`Separator` — a visible divider.

Build an embed::

    from kingdoms.discord.ui import UIEmbed, Text
    embed = UIEmbed(title="Kingdoms — Status", color=BLURPLE)
        .field("Uptime", "1h 2m")

Build a Components V2 layout::

    from kingdoms.discord.ui import UILayout, Container, Section, Text, Button, Row, Separator
    layout = (
        UILayout()
        .add(Container(accent=BLURPLE)
             .add(Text("# 🚀 Deployed"))
             .add(Section(Text("**Services**"), buttons=[Button("PR", url)]))
             .add(Separator())
             .add(Row(Button("Pipeline", run_url), Button("Image", pkg_url)))
             .build())
    )

Reliability guarantees (enforced here, not at Discord's door):

- the 4000-character shared TextDisplay budget is checked at build
  time (Discord rejects the message otherwise);
- the 40-component cap is checked;
- a Section accessory is only ever a link Button or a Thumbnail
  (Discord rejects anything else);
- V2 messages carry no ``content`` — text lives in TextDisplays.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord
from discord import SeparatorSpacing

__all__ = [
    "BLURPLE",
    "GREEN",
    "Button",
    "Container",
    "Row",
    "Section",
    "Separator",
    "Text",
    "Thumbnail",
    "UIEmbed",
    "UILayout",
    "UILayoutError",
]

BLURPLE = 0x5865F2
GREEN = 0x57F287

TEXT_BUDGET = 4000
COMPONENT_BUDGET = 40


class UILayoutError(ValueError):
    """Raised when a layout violates a Discord V2 constraint at build time."""


def _check_budget(text_total: int, components: int) -> None:
    if text_total > TEXT_BUDGET:
        raise UILayoutError(
            f"layout text exceeds the shared TextDisplay budget: {text_total} > {TEXT_BUDGET} characters"
        )
    if components > COMPONENT_BUDGET:
        raise UILayoutError(f"layout exceeds the component budget: {components} > {COMPONENT_BUDGET}")


@dataclass(frozen=True, slots=True)
class Button:
    """A link button (label + URL). Interactive buttons use ButtonRef + a view."""

    label: str
    url: str
    emoji: str = ""

    def _to_discord(self) -> discord.ui.Button[Any]:
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=self.label,
            style=discord.ButtonStyle.link,
            url=self.url,
            emoji=discord.PartialEmoji.from_str(self.emoji) if self.emoji else None,
        )
        return button


@dataclass(frozen=True, slots=True)
class Thumbnail:
    """A thumbnail (Section accessory only, per Discord)."""

    url: str


@dataclass(frozen=True, slots=True)
class Text:
    """A text block (full markdown, counted against the 4000-char budget)."""

    content: str


@dataclass(frozen=True, slots=True)
class Separator:
    """A visible divider."""

    small: bool = False


class Section:
    """Text blocks (max 3) with one accessory: a link Button or a Thumbnail.

    Built as Section(Text(...), Text(...), button=Button(...)) or
    Section(Text(...), thumbnail=Thumbnail(...)).
    """

    __slots__ = ("button", "texts", "thumbnail")

    def __init__(
        self,
        *texts: Text,
        button: Button | None = None,
        thumbnail: Thumbnail | None = None,
    ) -> None:
        if not 1 <= len(texts) <= 3:
            raise UILayoutError(f"a Section holds 1 to 3 text blocks, got {len(texts)}")
        if button is None and thumbnail is None:
            raise UILayoutError("a Section needs an accessory: button=Button(...) or thumbnail=Thumbnail(...)")
        if sum(1 for a in (button, thumbnail) if a is not None) > 1:
            raise UILayoutError("a Section accepts at most one accessory (Button or Thumbnail)")
        self.texts = texts
        self.button = button
        self.thumbnail = thumbnail


class Row:
    """A row of link buttons (max 5), built as Row(Button(...), ...)."""

    __slots__ = ("buttons",)

    def __init__(self, *buttons: Button) -> None:
        if not 1 <= len(buttons) <= 5:
            raise UILayoutError(f"an ActionRow holds 1 to 5 buttons, got {len(buttons)}")
        self.buttons = buttons


@dataclass(frozen=True, slots=True)
class Container:
    """An accent-colored card holding blocks."""

    blocks: tuple[Any, ...] = ()
    accent: int | None = None

    def add(self, *blocks: Any) -> Container:
        """Return a copy of the container with the blocks appended."""
        return Container(blocks=self.blocks + blocks, accent=self.accent)


@dataclass
class _LayoutState:
    text: int = 0
    components: int = 0


class UILayout:
    """A Components V2 layout builder (ADR-0009 rich UI)."""

    def __init__(self) -> None:
        self._containers: list[Container] = []

    def add(self, container: Container) -> UILayout:
        """Append a container to the layout."""
        self._containers.append(container)
        return self

    def build(self) -> discord.ui.LayoutView:
        """Assemble the declared containers into a Components V2 view."""
        view = discord.ui.LayoutView()
        state = _LayoutState()
        for container in self._containers:
            view.add_item(_build_container(container, state))
        _check_budget(state.text, state.components)
        return view


def _build_container(container: Container, state: _LayoutState) -> discord.ui.Container[discord.ui.LayoutView]:
    items: list[Any] = []
    for block in container.blocks:
        items.append(_build_block(block, state))
    built: discord.ui.Container[discord.ui.LayoutView] = discord.ui.Container(
        *items,
        accent_colour=container.accent,
    )
    state.components += 1
    return built


def _build_block(block: Any, state: _LayoutState) -> Any:
    if isinstance(block, Text):
        state.text += len(block.content)
        state.components += 1
        return discord.ui.TextDisplay(block.content)
    if isinstance(block, Separator):
        state.components += 1
        return discord.ui.Separator(
            visible=True,
            spacing=SeparatorSpacing.small if block.small else SeparatorSpacing.large,
        )
    if isinstance(block, Row):
        state.components += 1 + len(block.buttons)
        return discord.ui.ActionRow(*[b._to_discord() for b in block.buttons])
    if isinstance(block, Section) and block.button is None and block.thumbnail is None:
        raise UILayoutError(
            "a Section needs an accessory (button= or thumbnail=) — use Text for full-width text"
        )
    if isinstance(block, Section):
        state.components += 2
        for text in block.texts:
            state.text += len(text.content)
        texts: list[discord.ui.TextDisplay[discord.ui.LayoutView]] = [
            discord.ui.TextDisplay(t.content) for t in block.texts
        ]
        accessory: Any
        if block.button is not None:
            accessory = block.button._to_discord()
        else:
            accessory = discord.ui.Thumbnail(block.thumbnail.url if block.thumbnail else "")
        section: discord.ui.Section[discord.ui.LayoutView] = discord.ui.Section(*texts, accessory=accessory)
        return section
    raise UILayoutError(f"unsupported layout block: {type(block).__name__}")


class UIEmbed:
    """An embed builder (ADR-0009 light informational output)."""

    def __init__(self, title: str, color: int = BLURPLE, description: str = "") -> None:
        self._embed = discord.Embed(title=title, color=color)
        if description:
            self._embed.description = description

    def field(self, name: str, value: str, inline: bool = True) -> UIEmbed:
        """Append a field to the embed."""
        self._embed.add_field(name=name, value=value, inline=inline)
        return self

    def footer(self, text: str) -> UIEmbed:
        """Set the embed footer."""
        self._embed.set_footer(text=text)
        return self

    def build(self) -> discord.Embed:
        """Assemble the embed, checking the character budget."""
        total = len(self._embed.title or "") + len(self._embed.description or "")
        for f in self._embed.fields or []:
            total += len(f.name or "") + len(f.value or "")
        if total > TEXT_BUDGET:
            raise UILayoutError(f"embed exceeds the character budget: {total} > {TEXT_BUDGET}")
        return self._embed
