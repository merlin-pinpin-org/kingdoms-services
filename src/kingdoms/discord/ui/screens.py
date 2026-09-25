"""UI screens: archetypes and pagination built on the UI SDK.

The factory bricks (``kingdoms.discord.ui.factory``) compose freely but
still require layout thinking. This module offers the **archetypes** —
ready-made screen shapes the features (and later the game designer's
screen catalog) instantiate with data instead of layout code:

- :class:`Ranking` — a leader board: podium + paged rows, built as a
  Components V2 container. Rendered through :func:`render_ranking`.
- :func:`build_config_panel` — a settings panel: one section per
  setting group, a select menu to pick a group, and Apply/Reset
  :class:`Action` buttons.
- :func:`build_match_report` — a structured match report: header,
  score section, paged detail sections.

Pagination is generic: :func:`paginate` splits any list of blocks
into pages and renders a page as a standalone layout with Previous /
Next actions that edit the message in place (UPDATE_MESSAGE) —
Discord's expected UX for paginated V2 messages.

Every archetype funnels back through the factory, so the build-time
guarantees (text and component budgets, custom_id convention,
accessory constraints) hold for archetypes too — a feature using an
archetype cannot build an invalid screen.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import discord

from kingdoms.discord.ui.factory import (
    BLURPLE,
    Action,
    Button,
    Container,
    Option,
    Row,
    Section,
    SelectMenu,
    Separator,
    Text,
    Thumbnail,
    UILayout,
    UILayoutError,
)

Handler = Callable[[discord.Interaction], Awaitable[None]]


async def _noop_choose(interaction: discord.Interaction, values: list[str]) -> None:
    """Default select handler when a setting has no on_change hook."""
    return None

__all__ = [
    "PAGE_SIZE",
    "Option",
    "PaginatedScreen",
    "Ranking",
    "build_config_panel",
    "build_match_report",
    "render_ranking",
]


PAGE_SIZE = 10
_MEDALS = ("🥇", "🥈", "🥉")


class PaginatedScreen:
    """A paged screen: page blocks + Previous/Next actions editing in place."""

    def __init__(
        self,
        pages: Sequence[Sequence[Any]],
        *,
        mod: str,
        title: str,
        page_label: str = "Page",
        prev_label: str = "◀ Prev",
        next_label: str = "Next ▶",
    ) -> None:
        if not pages:
            raise UILayoutError("a paginated screen needs at least one page")
        self.pages = list(pages)
        self.mod = mod
        self.title = title
        self.page_label = page_label
        self.prev_label = prev_label
        self.next_label = next_label
        self.current = 0

    def _page_container(self, index: int) -> Any:
        blocks = list(self.pages[index])
        buttons: list[Action] = []
        if index > 0:
            buttons.append(Action(self.prev_label, f"{self.mod}:page:prev", self.on_prev))
        if index < len(self.pages) - 1:
            buttons.append(Action(self.next_label, f"{self.mod}:page:next", self.on_next))
        container = Container(accent=BLURPLE).add(Text(f"# {self.title}"))
        container = container.add(*blocks)
        footer = f"-# {self.page_label} {index + 1}/{len(self.pages)}"
        if buttons:
            container = container.add(Separator()).add(Row(*buttons))
        container = container.add(Text(footer))
        return container

    def render(self) -> discord.ui.LayoutView:
        """Render the current page as a Components V2 layout."""
        return UILayout().add(self._page_container(self.current)).build()

    async def _edit_in_place(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=self.render())

    async def on_prev(self, interaction: discord.Interaction) -> None:
        """Move one page back and edit the message in place."""
        if self.current > 0:
            self.current -= 1
        await self._edit_in_place(interaction)

    async def on_next(self, interaction: discord.Interaction) -> None:
        """Move one page forward and edit the message in place."""
        if self.current < len(self.pages) - 1:
            self.current += 1
        await self._edit_in_place(interaction)


class Ranking:
    """A leader board: entries + optional podium, rendered as a paged screen."""

    def __init__(
        self,
        title: str,
        entries: Sequence[tuple[str, str]],
        *,
        unit: str = "",
        emoji: str = "🏆",
    ) -> None:
        if not entries:
            raise UILayoutError("a ranking needs at least one entry")
        self.title = f"{emoji} {title}"
        self.entries = list(entries)
        self.unit = unit

    def _rows_text(self, start: int, entries: Sequence[tuple[str, str]]) -> str:
        lines: list[str] = []
        for offset, (name, value) in enumerate(entries):
            rank = start + offset + 1
            medal = _MEDALS[rank - 1] if rank <= 3 else f"`#{rank}`"
            value_part = f"{value} {self.unit}".rstrip()
            lines.append(f"{medal} **{name}** — {value_part}")
        return "\n".join(lines)

    def pages(self, page_size: int = PAGE_SIZE) -> list[list[Any]]:
        """Split the entries into page blocks (podium on page 1 only)."""
        page_blocks: list[list[Any]] = []
        total = len(self.entries)
        for start in range(0, total, page_size):
            chunk = self.entries[start : start + page_size]
            blocks: list[Any] = []
            if start == 0 and total >= 3:
                podium = " · ".join(
                    f"{_MEDALS[i]} **{name}** ({value} {self.unit})".rstrip()
                    for i, (name, value) in enumerate(self.entries[:3])
                )
                blocks.append(Text(f"**{podium}**"))
                blocks.append(Separator())
                chunk = self.entries[3 : page_size]
                start = 3
            blocks.append(Text(self._rows_text(start, chunk)))
            page_blocks.append(blocks)
        return page_blocks


def render_ranking(ranking: Ranking, *, page_size: int = PAGE_SIZE) -> PaginatedScreen:
    """Render a Ranking as a paginated screen ready to send."""
    return PaginatedScreen(ranking.pages(page_size), mod="ranking", title=ranking.title)


def build_config_panel(
    title: str,
    settings: Sequence[dict[str, Any]],
    *,
    mod: str,
    on_apply: Handler,
    on_reset: Handler,
) -> discord.ui.LayoutView:
    """Build a settings panel: a select menu per setting + Apply/Reset.

    ``settings`` entries: ``{"key": str, "label": str, "value": str,
    "options": [(label, value), ...], "on_change": async (interaction,
    values) -> None}``. ``on_apply``/``on_reset`` are async callbacks
    receiving the interaction (wired as Action buttons).
    """
    blocks: list[Any] = [Text(f"# ⚙️ {title}")]
    for entry in settings:
        key = str(entry["key"])
        label = str(entry["label"])
        value = str(entry["value"])
        options = tuple(
            Option(label=opt_label, value=opt_value)
            for opt_label, opt_value in entry["options"]
        )
        menu = SelectMenu(
            custom_id=f"{mod}:setting:{key}",
            options=options,
            on_choose=entry.get("on_change") or _noop_choose,
            placeholder=f"{label}: {value}",
        )
        blocks.append(Row(menu))
    blocks.append(Separator())
    blocks.append(
        Row(
            Action("✔ Apply", f"{mod}:config:apply", on_apply, style="success"),
            Action("↺ Reset", f"{mod}:config:reset", on_reset, style="secondary"),
        )
    )
    return UILayout().add(Container(accent=BLURPLE).add(*blocks)).build()


def build_match_report(
    title: str,
    summary: str,
    details: Sequence[tuple[str, str]],
    *,
    mod: str,
    links: Sequence[Button] = (),
    thumbnail_url: str = "",
) -> discord.ui.LayoutView:
    """Build a match report: header, summary section, paged detail rows.

    ``details`` are (label, value) pairs rendered as a section each.
    """
    container = Container(accent=BLURPLE).add(Text(f"# ⚔️ {title}"))
    accessory: Section | Text = (
        Section(Text(summary), thumbnail=Thumbnail(thumbnail_url))
        if thumbnail_url
        else Text(summary)
    )
    container = container.add(accessory)
    if details:
        container = container.add(Separator())
        container = container.add(Text("\n".join(f"**{k}** {v}" for k, v in details)))
    if links:
        container = container.add(Row(*links))
    return UILayout().add(container).build()
