"""Architectural guard: Discord's modal-component constraints.

Discord's new modal components require every select inside a modal to be
wrapped in a Label component (type 18) — a bare select in ``Modal.add_item``
is silently rejected by the API: the button that opens the modal seems
unresponsive. The rule landed for real on the profile surface (the add and
remove modals), and this test keeps it true for every modal of the codebase.

The check is structural and runtime-based at once: it instantiates every
``discord.ui.Modal`` subclass found in ``kingdoms.discord`` (and modsdk),
walks its children, and fails when a ``Select``-shaped item is reachable
without its Label wrapper — i.e. the serialized payload carries a bare
select component inside an action row instead of a label component.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path
from typing import Any

import discord
import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def _modal_classes() -> list[type[discord.ui.Modal]]:
    """Import every discord module and collect the Modal subclasses."""
    modals: list[type[discord.ui.Modal]] = []
    import kingdoms

    for module_info in pkgutil.walk_packages(kingdoms.__path__, prefix="kingdoms."):
        if not module_info.name.startswith(("kingdoms.discord", "kingdoms.modsdk")):
            continue
        try:
            module = importlib.import_module(module_info.name)
        except ImportError:
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, discord.ui.Modal) and obj is not discord.ui.Modal:
                if obj.__module__ == module_info.name:
                    modals.append(obj)
    return modals


def _instantiate(cls: type[discord.ui.Modal]) -> discord.ui.Modal | None:
    """Instantiate one modal class; None when its ctor needs live data."""
    try:
        return cls()
    except Exception:
        return None


def _bare_selects(modal: discord.ui.Modal) -> list[str]:
    """Return the custom ids of selects not wrapped in a Label."""
    bare: list[str] = []
    for child in modal.children:
        if isinstance(child, discord.ui.Label):
            continue
        if isinstance(child, discord.ui.Select):
            bare.append(str(child.custom_id or "?"))
    return bare


def test_every_modal_select_is_label_wrapped() -> None:
    """A modal holding a select must wrap it in a Label component."""
    checked = 0
    offenders: list[str] = []
    for cls in _modal_classes():
        modal = _instantiate(cls)
        if modal is None:
            continue
        checked += 1
        for custom_id in _bare_selects(modal):
            offenders.append(f"{cls.__name__}: bare select `{custom_id}`")
    assert checked > 0, "no modal could be instantiated — the guard covers nothing"
    assert not offenders, (
        "Discord rejects bare selects in modals (the opening button seems dead): "
        + "; ".join(offenders)
    )


def test_modal_payload_uses_label_components() -> None:
    """The serialized components carry type 18 labels around selects."""
    from discord import SelectOption

    from kingdoms.discord.home import ProfileAddAccountModal, ProfileRemoveAccountModal

    add = ProfileAddAccountModal([SelectOption(label="AoE2", value="aoe2")])
    remove = ProfileRemoveAccountModal([SelectOption(label="A", value="aoe2|1")])
    for modal in (add, remove):
        payload = modal.to_components()
        assert any(component.get("type") == 18 for component in payload), (
            f"{type(modal).__name__} payload lacks a Label component (type 18)"
        )


def test_modal_select_survives_refresh_lookup() -> None:
    """``Modal._refresh`` must find the select by custom_id (submission read)."""
    from discord import SelectOption

    from kingdoms.discord.home import ProfileRemoveAccountModal

    modal = ProfileRemoveAccountModal([SelectOption(label="A", value="aoe2|1")])
    custom_id = modal.accounts.custom_id
    found = [item for item in modal.walk_children() if getattr(item, "custom_id", None) == custom_id]
    assert found and found[0] is modal.accounts, "the select must stay reachable for the refresh"


@pytest.mark.parametrize(
    "max_values,option_count",
    [(1, 1), (2, 2), (5, 5)],
)
def test_modal_multiselect_allows_all_options(max_values: int, option_count: int) -> None:
    """A multi-select modal must allow choosing every option at once."""
    from discord import SelectOption

    from kingdoms.discord.home import ProfileRemoveAccountModal

    options = [SelectOption(label=f"opt{i}", value=f"aoe2|{i}") for i in range(option_count)]
    modal = ProfileRemoveAccountModal(options)
    assert modal.accounts.max_values == option_count


def test_source_scans_for_bare_modal_selects() -> None:
    """The source of every Modal.add_item never passes a Select directly."""
    pattern_select = "self.add_item(self."
    for path in SRC.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "discord.ui.Modal" not in src:
            continue
        in_modal = False
        for line in src.splitlines():
            if line.startswith("class ") and "Modal" in line:
                in_modal = True
            elif line and not line[0].isspace():
                in_modal = False
            if not in_modal:
                continue
            if pattern_select in line and "Select" in line and "Label" not in line:
                pytest.fail(f"{path}:{line.strip()} adds a bare select to a modal")


# --- Discord's documented payload limits (Component Reference, oct. 2025) ---
_LABEL_TEXT_MAX = 45
_LABEL_DESCRIPTION_MAX = 100
_PLACEHOLDER_MAX = 100
_TEXT_INPUT_LABEL_MAX = 45


def test_modal_payloads_respect_discord_limits() -> None:
    """Every modal's serialized payload respects Discord's documented limits.

    The 50035 rejections are silent for the user (the modal never opens)
    and invisible to the logs \u2014 only a payload-level guard catches
    them before the deploy: label text <= 45, description <= 100,
    placeholder <= 100, TextInput label <= 45.
    """
    offenders: list[str] = []

    def check(item: Any, where: str) -> None:
        text = getattr(item, "text", None)
        if text is not None and len(text) > _LABEL_TEXT_MAX:
            offenders.append(f"{where}: Label text {len(text)} > {_LABEL_TEXT_MAX}")
        description = getattr(item, "description", None)
        if description is not None and len(description) > _LABEL_DESCRIPTION_MAX:
            offenders.append(f"{where}: Label description {len(description)} > {_LABEL_DESCRIPTION_MAX}")
        placeholder = getattr(item, "placeholder", None)
        if placeholder is not None and len(placeholder) > _PLACEHOLDER_MAX:
            offenders.append(f"{where}: placeholder {len(placeholder)} > {_PLACEHOLDER_MAX}")
        inner_label = getattr(item, "label", None)
        if type(item).__name__ == "TextInput" and inner_label and len(inner_label) > _TEXT_INPUT_LABEL_MAX:
            offenders.append(f"{where}: TextInput label {len(inner_label)} > {_TEXT_INPUT_LABEL_MAX}")

    for cls in _modal_classes():
        modal = _instantiate(cls)
        if modal is None:
            continue
        for child in modal.children:
            check(child, cls.__name__)
            inner = getattr(child, "component", None)
            if inner is not None:
                check(inner, f"{cls.__name__} (inner)")

    assert not offenders, "Discord rejects the modal payload (50035, the modal never opens): " + "; ".join(offenders)


def test_modal_textinputs_keep_the_proven_action_row_pattern() -> None:
    """TextInput rides the repo's proven pattern: naked in an ActionRow.

    The legacy pattern (TextInput with its own ``label`` in an implicit
    ActionRow) works everywhere in production; wrapping a TextInput in
    a Label component is NOT supported by discord.py 2.7's serializer
    (the inner deprecated ``label`` leaks into the payload and Discord
    rejects the modal \u2014 regression of 2026-10-15). Only selects need
    the Label wrapper.
    """
    offenders: list[str] = []
    for cls in _modal_classes():
        modal = _instantiate(cls)
        if modal is None:
            continue
        for child in modal.children:
            if isinstance(child, discord.ui.Label) and isinstance(child.component, discord.ui.TextInput):
                offenders.append(f"{cls.__name__}: TextInput wrapped in a Label")
    assert not offenders, (
        "TextInput must stay naked (ActionRow pattern), only selects ride a Label: " + "; ".join(offenders)
    )
