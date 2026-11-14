"""The menu/panel archetype set — the SDK's reusable shapes (#380).

The first client's shapes (kingdoms), generalized from good bases —
not an absolute model of good ideas. Each archetype is a thin builder
over the platform's seams (the uniform card contract #321, the view
footer, the declared views) and carries its budget traps in the unit
tests: the 4000-char text budget, the modal selects
(``required=False`` + ``min_values=0``), the 10003 vanished channel,
and the 5-top-level-components-per-modal cap.

- **list panel**: a header, one block per entry, the id footer — the
  catalog/forum shape (a list of cards rides the uniform card contract)
- **candidates table**: a markdown table — the dashboard shape (rows x
  columns, clamped to the text budget)
- **detail card**: the uniform card contract (``entity_cards``) — the
  archetype IS the existing seam, documented here
- **admin panel**: the admin bar (``entity_admin``) + the panel content
- **wizard/modal picker**: the wizard engine (``ui.wizard``)
- **navigation footer**: ``add_view_footer`` — every declared view's
  signature, one line
"""

from __future__ import annotations

import logging

import discord

from botforge.discord.ui.view_footer import add_view_footer

logger = logging.getLogger("botforge.discord.ui.archetypes")

# The components-v2 text budget trap: one TextDisplay caps at 4000
# chars — the builders clamp, never raise
_TEXT_BUDGET = 4000


def _clamped(text: str, budget: int = _TEXT_BUDGET) -> str:
    """Clamp one text to the budget, the tail marked when it had to cut."""
    if len(text) <= budget:
        return text
    cut = text[: budget - 1].rsplit("\n", 1)[0]
    return f"{cut}\n…"


def list_panel(
    title: str,
    entries: list[str],
    view_id: str,
    *,
    description: str = "",
    accent: int | None = None,
) -> discord.ui.LayoutView:
    """Build the LIST PANEL archetype: a header, one block per entry, the id footer.

    The catalog/forum shape — a list of things, each visible at a
    glance, the view's identity in the footer (the declared-views
    contract). The text budget is clamped per block, never raised.
    """
    view = discord.ui.LayoutView(timeout=None)
    blocks: list[discord.ui.Item[discord.ui.LayoutView]] = [
        discord.ui.TextDisplay(f"# {title}")
    ]
    if description:
        blocks.append(discord.ui.TextDisplay(_clamped(description)))
    blocks.append(discord.ui.Separator())
    if not entries:
        blocks.append(discord.ui.TextDisplay("—"))
    for entry in entries:
        blocks.append(discord.ui.TextDisplay(_clamped(str(entry))))
        blocks.append(discord.ui.Separator())
    container = discord.ui.Container(*blocks)
    if accent is not None:
        container.accent_colour = discord.Colour(accent)
    view.add_item(container)
    add_view_footer(view, view_id)
    return view


def candidates_table(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    view_id: str,
    *,
    description: str = "",
) -> discord.ui.LayoutView:
    """Build the CANDIDATES TABLE archetype: a markdown table, the dashboard shape.

    Rows x columns as a Discord markdown table, clamped to the text
    budget (a wide table cuts at the last full row, the tail marked);
    the id footer rides the declared-views contract.
    """
    header = f"# {title}\n"
    if description:
        header += f"{description}\n"
    if not rows:
        body = f"{header}\n| {' | '.join(columns)} |\n|{'|'.join(['---'] * len(columns))}|\n"
    else:
        lines = [f"{header}", f"| {' | '.join(columns)} |"]
        lines.append(f"|{'|'.join(['---'] * len(columns))}|")
        for row in rows:
            lines.append(f"| {' | '.join(str(cell) for cell in row)} |")
            candidate = "\n".join(lines)
            if len(candidate) > _TEXT_BUDGET - 2:
                lines.pop()
                lines.append("…")
                break
        body = "\n".join(lines)
    body = _clamped(body)
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(discord.ui.TextDisplay(body)))
    add_view_footer(view, view_id)
    return view


def wizard_picker_note() -> str:
    """Return the modal-picker trap reminder (documented, tested at the seam).

    A select inside a MODAL must ride ``required=False`` +
    ``min_values=0`` (a required select the user cannot satisfy
    blocks the submit); the wizard engine's selects live in VIEWS,
    where the trap does not apply — the modal form carries it.
    """
    return "modal selects: required=False + min_values=0 (discord-components skill)"


__all__ = [
    "add_view_footer",
    "candidates_table",
    "list_panel",
    "wizard_picker_note",
]
