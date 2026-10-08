"""Generic season-wizard engine: one question at a time.

The core owns the wizard **machinery** — the accumulated state, the
ephemeral step plumbing (a step may open from a fresh interaction or
from a callback on an already-answered one), the select/confirm
builders and the integer validation — while each seasonal mod provides
its own **steps** through these building blocks. The mod keeps full
control of what is asked and what the answers do; the engine only
makes the flow consistent across mods (ladder, kingdoms, any future
seasonal mod).

The state lives in the ephemeral interaction chain (each step's
callback carries the same ``WizardState`` forward); no server-side
session store is needed.

Reference: docs/architecture/mods.md (kingdoms repo) — the core/mod
split rule; the season wizard is core machinery, the steps are mod
content.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import discord

logger = logging.getLogger("kingdoms.discord.season_wizard")

WIZARD_TIMEOUT = 600


@dataclass
class WizardState:
    """The accumulated answers of one wizard run.

    ``subject_id`` is the mod-defined object the season is created for
    (a ladder id, a kingdoms season id, ...); ``answers`` carries the
    raw values, ``labels`` their human-readable form for the recap.
    """

    subject_id: str
    name: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value: Any, label: str | None = None) -> None:
        """Record one answer (and its recap label)."""
        self.answers[key] = value
        if label is not None:
            self.labels[key] = label

    def get(self, key: str, default: Any = None) -> Any:
        """Return one recorded answer (or the default)."""
        return self.answers.get(key, default)


async def send_step(
    interaction: discord.Interaction, content: str, view: discord.ui.View
) -> None:
    """Send one step's ephemeral view, whatever the interaction state.

    A step can open from a fresh interaction (a button click in an
    admin panel) or from a callback on an already-answered one — the
    response/followup split is the engine's job, not the mod's.
    """
    if interaction.response.is_done():
        await interaction.followup.send(content=content, view=view, ephemeral=True)
    else:
        await interaction.response.send_message(content=content, view=view, ephemeral=True)


async def ask_select(
    interaction: discord.Interaction,
    *,
    placeholder: str,
    options: list[discord.SelectOption],
    content: str,
    on_pick: Callable[[discord.Interaction, str, str], Awaitable[None]],
    empty_label: str | None = None,
) -> None:
    """Ask one question through a select menu (max 25 Discord options).

    ``on_pick(inner, value, label)`` receives the chosen value and its
    human-readable label; it chains to the next step. An empty option
    list falls back to a single ``empty_label`` entry so the flow can
    continue (the mod decides what "none" means).
    """
    if not options and empty_label is not None:
        options = [discord.SelectOption(label=empty_label, value="none")]
    view = discord.ui.View(timeout=WIZARD_TIMEOUT)
    select: discord.ui.Select[Any] = discord.ui.Select(
        placeholder=placeholder, options=options[:25]
    )

    async def _pick(inner: discord.Interaction) -> None:
        chosen = (getattr(inner, "values", None) or [""])[0]
        label = next((o.label for o in options if o.value == chosen), chosen)
        await on_pick(inner, chosen, label)

    select.callback = _pick  # type: ignore[method-assign, assignment]
    view.add_item(select)
    await send_step(interaction, content, view)


async def ask_confirm(
    interaction: discord.Interaction,
    *,
    content: str,
    button_label: str,
    on_confirm: Callable[[discord.Interaction], Awaitable[None]],
    style: discord.ButtonStyle = discord.ButtonStyle.success,
) -> None:
    """Show a recap and one confirm button; ``on_confirm`` finalizes.

    The recap content is mod-built (it knows its own steps' labels);
    the engine only owns the button plumbing.
    """
    view = discord.ui.View(timeout=WIZARD_TIMEOUT)
    button: discord.ui.Button[Any] = discord.ui.Button(label=button_label, style=style)

    async def _confirm(inner: discord.Interaction) -> None:
        await on_confirm(inner)

    button.callback = _confirm  # type: ignore[method-assign, assignment]
    view.add_item(button)
    await send_step(interaction, content, view)


def parse_int(raw: str) -> int | None:
    """Parse one wizard text input into an int; None when invalid."""
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return None
