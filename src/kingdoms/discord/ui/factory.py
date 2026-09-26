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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import discord
from discord import SeparatorSpacing

Handler = Callable[[discord.Interaction], Awaitable[None]]
SelectHandler = Callable[[discord.Interaction, list[str]], Awaitable[None]]

__all__ = [
    "BLURPLE",
    "GREEN",
    "Action",
    "Button",
    "Container",
    "Option",
    "Row",
    "Section",
    "SelectMenu",
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


def _check_custom_id(custom_id: str) -> None:
    """Enforce the repo custom ID convention: ``<mod>:<component>:<payload>``."""
    parts = custom_id.split(":")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise UILayoutError(f"custom_id {custom_id!r} must follow '<mod>:<component>:<payload>'")


def _check_budget(text_total: int, components: int) -> None:
    if text_total > TEXT_BUDGET:
        raise UILayoutError(
            f"layout text exceeds the shared TextDisplay budget: {text_total} > {TEXT_BUDGET} characters"
        )
    if components > COMPONENT_BUDGET:
        raise UILayoutError(f"layout exceeds the component budget: {components} > {COMPONENT_BUDGET}")


@dataclass(frozen=True, slots=True)
class Button:
    """A link button (label + URL)."""

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
class Action:
    """An interactive button: label + custom_id + async callback.

    The custom_id must follow the repo convention ``<mod>:<component>:<payload>``
    (e.g. ``admin:ping:`` or ``ranking:page:next``) — checked at build time.
    The handler is an async callable receiving the interaction; it is wired
    into the generated LayoutView, so dispatch flows through the real
    discord.py machinery.
    """

    label: str
    custom_id: str
    on_click: Handler
    emoji: str = ""
    style: str = "primary"

    def __post_init__(self) -> None:
        """Validate the custom_id convention and the button style."""
        _check_custom_id(self.custom_id)
        if self.style not in ("primary", "secondary", "success", "danger"):
            raise UILayoutError(f"unknown button style: {self.style!r}")

    def _to_discord(self) -> discord.ui.Button[Any]:
        styles = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
        }
        button: discord.ui.Button[Any] = discord.ui.Button(
            label=self.label,
            style=styles[self.style],
            custom_id=self.custom_id,
            emoji=discord.PartialEmoji.from_str(self.emoji) if self.emoji else None,
        )
        button.callback = self.on_click  # type: ignore[method-assign, assignment]
        return button


@dataclass(frozen=True, slots=True)
class Option:
    """A select menu option: label + machine value (+ optional description)."""

    label: str
    value: str
    description: str = ""
    emoji: str = ""


@dataclass(frozen=True, slots=True)
class SelectMenu:
    """An interactive select menu: options + async callback on choose.

    The callback receives the interaction and the chosen values (str)
    — no digging through ``interaction.data`` in feature code.
    """

    custom_id: str
    options: tuple[Option, ...]
    on_choose: SelectHandler
    placeholder: str = ""
    min_values: int = 1
    max_values: int = 1

    def __post_init__(self) -> None:
        """Validate the custom_id convention and the options bounds."""
        _check_custom_id(self.custom_id)
        if not 1 <= len(self.options) <= 25:
            raise UILayoutError(f"a SelectMenu holds 1 to 25 options, got {len(self.options)}")
        if not 1 <= self.min_values <= self.max_values <= len(self.options):
            raise UILayoutError(
                f"SelectMenu values bounds are invalid: min={self.min_values} max={self.max_values}"
            )

    def _to_discord(self) -> discord.ui.Select[Any]:
        select: discord.ui.Select[Any] = discord.ui.Select(
            custom_id=self.custom_id,
            placeholder=self.placeholder or None,
            min_values=self.min_values,
            max_values=self.max_values,
            options=[
                discord.SelectOption(
                    label=o.label,
                    value=o.value,
                    description=o.description or None,
                    emoji=discord.PartialEmoji.from_str(o.emoji) if o.emoji else None,
                )
                for o in self.options
            ],
        )
        async def _dispatch(interaction: discord.Interaction) -> None:
            data = getattr(interaction, "data", None) or {}
            values = [str(v) for v in data.get("values", [])]
            await self.on_choose(interaction, values)

        select.callback = _dispatch  # type: ignore[method-assign]
        return select


@dataclass(frozen=True, slots=True)
class ChannelSelect:
    """An interactive channel select: async callback on choose.

    The callback receives the interaction and the chosen channel ids
    (str) — no digging through ``interaction.data`` in feature code.
    ``channel_types`` restricts the picker (default: text channels
    only, the sensible default for message routing).
    """

    custom_id: str
    on_choose: SelectHandler
    placeholder: str = ""
    channel_types: tuple[Any, ...] = (discord.ChannelType.text,)

    def __post_init__(self) -> None:
        """Validate the custom_id convention."""
        _check_custom_id(self.custom_id)

    def _to_discord(self) -> discord.ui.ChannelSelect[Any]:
        import discord as _discord

        select: discord.ui.ChannelSelect[Any] = _discord.ui.ChannelSelect(
            custom_id=self.custom_id,
            placeholder=self.placeholder or None,
            channel_types=[_discord.ChannelType(t) for t in self.channel_types],
            min_values=1,
            max_values=1,
        )
        handler = self.on_choose

        async def _dispatch(interaction: _discord.Interaction) -> None:
            values = [str(c.id) for c in getattr(select, "values", [])]
            await handler(interaction, values)

        select.callback = _dispatch  # type: ignore[method-assign]
        return select


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
    """A row of interactive items (max 5): Buttons, Actions, SelectMenu.

    Discord wraps items in an ActionRow; a SelectMenu (or a
    ChannelSelect) is the only item in its row per Discord's layout rules.
    """

    __slots__ = ("buttons",)

    def __init__(self, *buttons: Button | Action | SelectMenu | ChannelSelect) -> None:
        if not 1 <= len(buttons) <= 5:
            raise UILayoutError(f"an ActionRow holds 1 to 5 items, got {len(buttons)}")
        if any(isinstance(b, (SelectMenu, ChannelSelect)) for b in buttons) and len(buttons) > 1:
            raise UILayoutError("a SelectMenu must be alone in its row")
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
    if isinstance(block, SelectMenu):
        state.components += 1
        return block._to_discord()
    if isinstance(block, ChannelSelect):
        state.components += 1
        return block._to_discord()
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
