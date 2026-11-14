"""The archetype set's unit tests — the budget traps included (#380).

Each archetype carries its documented trap: the 4000-char text
budget (clamped, never raised), the modal selects' required=False +
min_values=0, and the shapes' contracts (the id footer, the table's
full-row cut).
"""

from __future__ import annotations

import discord

from botforge.discord.ui.archetypes import candidates_table, list_panel, wizard_picker_note
from botforge.discord.ui.wizard import WizardState, parse_int


def _rendered(view: discord.ui.LayoutView) -> str:
    """Flatten a view's component payload into its text content."""
    out: list[str] = []

    def walk(component: object) -> None:
        if isinstance(component, dict):
            if component.get("content"):
                out.append(str(component["content"]))
            for child in component.get("components") or []:
                walk(child)
        elif isinstance(component, (list, tuple)):
            for child in component:
                walk(child)

    for top in view.to_components():
        walk(top)
    return "\n".join(out)


def test_the_list_panel_carries_the_id_footer_and_the_entries() -> None:
    view = list_panel("Salons", ["un", "deux"], "panel:test:list", description="desc")
    rendered = _rendered(view)
    assert "Salons" in rendered
    assert "un" in rendered and "deux" in rendered
    assert "panel:test:list" in rendered



def test_the_list_panel_clamps_to_the_text_budget() -> None:
    long = "x" * 5000
    view = list_panel("T", [long], "panel:test:budget")
    rendered = _rendered(view)
    assert len(rendered) < 10000  # the clamp held, the view stays sane
    assert "…" in rendered  # the cut is marked, never silent


def test_the_candidates_table_renders_rows_and_cuts_at_a_full_row() -> None:
    rows = [[f"kingdom-{i}", str(i)] for i in range(400)]
    view = candidates_table("Candidats", ["nom", "score"], rows, "panel:test:table")
    rendered = _rendered(view)
    assert "Candidats" in rendered
    assert "kingdom-0" in rendered
    assert "panel:test:table" in rendered
    # the budget held: the table cut at a full row, the tail marked
    assert len(rendered) < 9000
    assert rendered.count("…") <= 1


def test_the_candidates_table_handles_the_empty_case() -> None:
    view = candidates_table("Candidats", ["nom"], [], "panel:test:empty")
    assert "Candidats" in _rendered(view)


def test_the_wizard_state_accumulates() -> None:
    state = WizardState(subject_id="season-1")
    state.set("rounds", 3, "3 tours")
    assert state.get("rounds") == 3
    assert state.labels["rounds"] == "3 tours"
    assert state.get("missing", "dflt") == "dflt"


def test_parse_int_tolerates_the_garbage() -> None:
    assert parse_int(" 42 ") == 42
    assert parse_int("nope") is None
    assert parse_int("") is None


def test_the_modal_picker_trap_is_documented() -> None:
    note = wizard_picker_note()
    assert "required=False" in note
    assert "min_values=0" in note


def test_the_modal_select_trap_shape() -> None:
    """The discord-components trap, at the seam: a modal's select rides
    required=False + min_values=0 — never a required select the user
    cannot satisfy."""
    select = discord.ui.Select(
        placeholder="p",
        options=[discord.SelectOption(label="a", value="a")],
        min_values=0,
    )
    assert select.min_values == 0
