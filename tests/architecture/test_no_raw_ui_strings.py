"""No raw user-facing strings: every UI literal renders through the catalog.

A literal inside a user-facing call (TextDisplay, send_message,
followup.send, Button label, SelectOption label, modal title) must
either be an f-string/key built from the i18n helpers (tr, _t, reply,
_dm_tr, _tr, _label — the localized helpers) or live in the allowlist
below (ids, numbers, symbols — never prose).

The 2026-10-20 session lesson: untranslated strings and dead legacy
prose kept leaking onto the live surfaces for hours; this guard makes
the leak a test failure instead of a user report.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The enforced scope: the modules held to the no-raw-strings rule today.
# The games/pools flows carry ~130 legacy French literals — tracked in
# kingdoms-services#273; each module joins this set as its strings
# migrate to the catalog.
ENFORCED = {
    "admin_dm_panel.py",
    "admin_tree.py",
    "views_routing.py",
    "views_declared.py",
    "entity_admin_menu.py",
    "staff.py",
    "home.py",
    "locales.py",
}

# The calls that render user-facing strings; the first positional arg
# (or the label/title/placeholder kwarg) is checked.
UI_CALLS = {
    "TextDisplay",
    "send_message",
    "send",
    "edit",
}

# The helpers that prove a string renders localized: a call through one
# of them is compliant whatever the literal is.
LOCALIZED_HELPERS = re.compile(
    r"\b(_?tr|_t|reply|_dm_tr|_label|render|localized)\b"
)

# Prose-free literals: ids, format keys, symbols, punctuation.
ALLOWED_LITERAL = re.compile(r"^[^A-Za-zÀ-ÿ]*$")

# Known non-prose literals (ids, marks, separators) — anything readable
# that is not a translated surface.
ALLOWLIST = {
    # the staff decision notice: built at edit time (no interaction
    # context for the catalog helpers), French-only by design
    "## \U0001F6E1 Candidature staff",
    # view ids and footers
    "view:bot:admin",
    "panel:game:aoe2:live",
    "?",
    "-",
    "—",
}


def _is_localized(arg: ast.AST) -> bool:
    """Whether an argument renders through a localized helper."""
    if isinstance(arg, ast.Call):
        func = arg.func
        name = getattr(func, "attr", None) or getattr(func, "id", "")
        if LOCALIZED_HELPERS.search(name or ""):
            return True
        # f-string whose parts include a localized call
        return False
    if isinstance(arg, ast.JoinedStr):
        return any(
            isinstance(v, ast.FormattedValue) and _is_localized(v.value) for v in arg.values
        )
    return False


def _literal_of(arg: ast.AST) -> str | None:
    """The plain literal an argument carries, if it is one."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        parts = [v.value for v in arg.values if isinstance(v, ast.Constant)]
        return "".join(parts) if parts else ""
    return None


def _offenders_of(path: Path) -> list[str]:
    """Scan one module for raw user-facing literals."""
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", "")
            if name not in UI_CALLS:
                continue
            if not node.args:
                continue
            target = node.args[0]
            if _is_localized(target):
                continue
            literal = _literal_of(target)
            if literal is None:
                continue  # dynamic (a variable, a key built at runtime)
            if literal in ALLOWLIST or ALLOWED_LITERAL.match(literal):
                continue
            if not re.search(r"[A-Za-zÀ-ÿ]{3,}", literal):
                continue  # no readable prose
            found.append(f"{path.name}:{node.lineno}: {literal[:70]!r}")
    return found


def test_no_raw_ui_strings() -> None:
    offenders: list[str] = []
    for path in (ROOT / "src" / "kingdoms").rglob("*.py"):
        if "rpc_generated" in str(path) or path.name not in ENFORCED:
            continue
        offenders.extend(_offenders_of(path))
    assert not offenders, (
        "Raw user-facing strings (route them through the i18n catalog): "
        + "; ".join(offenders[:10])
    )
