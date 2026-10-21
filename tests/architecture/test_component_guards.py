"""Architectural guards: the two silent button-killers of 2026-10-16.

Two classes of failure passed the whole CI suite and only surfaced on the
test guild (each killed three admin buttons at once):

1. **Invalid emoji** — Discord's API rejects 50035 (Invalid Form Body,
   ``Invalid emoji``) for codepoints outside its emoji set (e.g. U+1F5F2
   "lightning", U+1F5C2 "card index dividers"). The button never opens;
   nothing user-facing says why. The pragmatic CI rule: the U+1F5xx
   Miscellaneous Symbols block is where almost all the invalid ones live —
   a small allowlist of known-good symbols guards it, everything else in
   that block fails the build.

2. **Double defer** — a callback that defers (or sends a modal) and then
   calls a handler that defers again raises ``InteractionResponded``; the
   click dies silently. The rule: a DynamicItem callback that dispatches
   to a `_handle_*` function from ``admin_persistent`` must not defer
   first — those handlers own their own acknowledgment.

Three more silent killers joined on 2026-10-17 (each killed a live
button or flow while the suite stayed green):

3. **Modal without ``on_submit``** — the language modal had no
   ``on_submit``: the submit died with "interaction failed" and zero
   logs. Every ``discord.ui.Modal`` subclass must define
   ``async def on_submit``.
4. **custom_id off its template** — a DynamicItem built with a
   custom_id its own template regex rejects raises ``ValueError`` at
   click time (the AccessGuildSelect 'picker' bug). The guard checks
   every constant custom_id built in a DynamicItem class against its
   template.
5. **Two selects in one action row** — a Select occupies a whole
   Discord action row; adding a second raises ``maximum number of
   children exceeded`` at render time (the Roles mapping bug). The
   guard fails the build on an ActionRow fed two Select/ChannelSelect.
"""

from __future__ import annotations

import ast
import pathlib
import re

# The known-good U+1F5xx symbols used on components today (validated live
# against Discord). Anything else in the block must be added here WITH a
# live validation first.
_GOOD_1F5XX = {
    0x1F512,  # lock
    0x1F513,  # open lock
    0x1F517,  # link
    0x1F527,  # wrench
    0x1F52A,  # knife (civs)
    0x1F52E,  # crystal ball
    0x1F570,  # clock
    0x1F576,  # sunglasses
    0x1F577,  # spider
    0x1F578,  # spider web
    0x1F579,  # joystick
    0x1F587,  # linked paperclips
    0x1F58A,  # pen
    0x1F58B,  # fountain pen
    0x1F5FA,  # world map
    0x1F5FB,  # mount fuji
    0x1F5FC,  # tokyo tower
    0x1F5FD,  # statue of liberty
    0x1F5DE,  # bookmark tabs (labels only — NOT valid as component emoji)
# 0x1F5AE rejected live (50035 Invalid emoji, 2026-10-21 test env)
    0x1F5D1,  # trash bin
}

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_component_emojis_are_discord_valid() -> None:
    """Every emoji in the 1F5xx symbol block must be API-validated.

    The block is where the API-invalid codepoints concentrate (U+1F5F2
    killed two buttons with 50035). A new symbol requires a live check
    before joining the allowlist.
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for char in text:
            cp = ord(char)
            if 0x1F500 <= cp <= 0x1F5FF and cp not in _GOOD_1F5XX:
                offenders.append(f"{path.name}: U+{cp:04X} {char!r}")
    assert not offenders, (
        "Discord rejects these emoji with 50035 Invalid emoji (the button never opens; "
        "the 2026-10-16 lesson): " + "; ".join(sorted(set(offenders)))
    )


_DEFER_RE = re.compile(r"await\s+interaction\.response\.defer\(")
_HANDLE_RE = re.compile(r"from\s+kingdoms\.discord\.admin_persistent\s+import\s+(_handle_\w+)")


def test_callbacks_do_not_double_defer() -> None:
    """A DynamicItem callback must not defer before a self-acking handler.

    ``_handle_roles`` (and every ``_handle_*`` in admin_persistent) owns
    its own acknowledgment — deferring before the call raises
    InteractionResponded and the click dies (the 2026-10-16 Roles bug).
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        text = path.read_text(encoding="utf-8")
        for match in _HANDLE_RE.finditer(text):
            # find the enclosing async def callback block
            start = text.rfind("async def callback", 0, match.start())
            if start == -1:
                continue
            # the defer must not appear between the callback start and the handler call
            block = text[start : match.start()]
            if _DEFER_RE.search(block):
                offenders.append(f"{path.name}: defers before {match.group(1)}")
    assert not offenders, (
        "These callbacks defer before calling a handler that acks itself "
        "(InteractionResponded — the 2026-10-16 Roles lesson): "
        + "; ".join(sorted(set(offenders)))
    )


def test_modals_define_on_submit() -> None:
    """Every Modal subclass must implement ``async def on_submit``.

    The 2026-10-17 language bug: a Modal without ``on_submit`` dies at
    submit with "interaction failed" and leaves zero logs — the suite
    cannot see it. A modal exists to be submitted.
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {
                b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
                for b in node.bases
            }
            if "Modal" not in bases:
                continue
            methods = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if "on_submit" not in methods:
                offenders.append(f"{path.name}:{node.name}")
    assert not offenders, (
        "These Modal subclasses never handle their submit (the click dies with "
        "'interaction failed', zero logs — the 2026-10-17 language lesson): "
        + "; ".join(sorted(offenders))
    )


def _dynamic_item_classes(tree: ast.Module) -> list[ast.ClassDef]:
    """Return every DynamicItem subclass of one module."""
    out: list[ast.ClassDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            target = base
            while isinstance(target, ast.Subscript):
                target = target.value
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name == "DynamicItem":
                out.append(node)
                break
    return out


def _resolve_str(expr: ast.expr, constants: dict[str, str]) -> str:
    """Resolve a template expression to its string shape.

    JoinedStr parts (rf-strings with ``{_NS}`` refs) resolve through
    the module's CONSTANT assignments; a f-string payload segment
    (``(?P<guild_id>...)``) stays literal.
    """
    if isinstance(expr, ast.Subscript):
        return _resolve_str(expr.value, constants)
    if isinstance(expr, ast.Constant):
        return str(expr.value)
    if isinstance(expr, ast.JoinedStr):
        out = []
        for part in expr.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                inner = part.value
                if isinstance(inner, ast.Constant):
                    out.append(str(inner.value))
                elif isinstance(inner, ast.Name):
                    out.append(constants.get(inner.id, "<VAR>"))
                else:
                    out.append("<VAR>")
        return "".join(out)
    return ""


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Map the module-level UPPER constants used inside templates."""
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.isupper():
                out[target.id] = _resolve_str(node.value, {})
    return out


def _class_template(node: ast.ClassDef, constants: dict[str, str]) -> re.Pattern[str] | None:
    """Compile a DynamicItem subclass's ``template`` keyword."""
    for kw in node.keywords:
        if kw.arg == "template":
            shape = _resolve_str(kw.value, constants)
            if shape:
                return re.compile(shape)
    return None


def _constant_custom_ids(node: ast.ClassDef, constants: dict[str, str]) -> list[str]:
    """Collect the custom_ids built in a class's methods (resolvable ones)."""
    out: list[str] = []
    for sub in ast.walk(node):
        for kw in getattr(sub, "keywords", None) or []:
            if kw.arg != "custom_id":
                continue
            shape = _resolve_str(kw.value, constants)
            if shape and "<VAR>" not in shape:
                out.append(shape)
    return out


def test_dynamic_item_custom_ids_match_their_template() -> None:
    """Every DynamicItem's constant custom_id must match its own template.

    The 2026-10-17 DM bug: AccessGuildSelect shipped the constant
    custom_id ``admin:dm:access:guild:picker`` while its template
    required digits — discord.py raises ``ValueError`` at every wire
    rebuild and the button dies silently.
    """
    offenders: list[str] = []
    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_constants(tree)
        for node in _dynamic_item_classes(tree):
            template = _class_template(node, constants)
            if template is None:
                continue
            for custom_id in _constant_custom_ids(node, constants):
                if not template.match(custom_id):
                    offenders.append(
                        f"{path.name}:{node.name} custom_id {custom_id!r} vs {template.pattern!r}"
                    )
    assert not offenders, (
        "These custom_ids fail their own DynamicItem template "
        "(discord.py raises ValueError at click time — the 2026-10-17 DM lesson): "
        + "; ".join(sorted(offenders))
    )


def _select_name(call: ast.Call) -> str:
    """Return the constructor name of an add_item child ("" when opaque)."""
    child = call.args[0] if call.args else None
    child_func = child.func if isinstance(child, ast.Call) else None
    if child_func is None:
        return ""
    return child_func.attr if isinstance(child_func, ast.Attribute) else getattr(child_func, "id", "")


def _created_row_names(stmt: ast.stmt) -> set[str]:
    """Return the *row variables a statement assigns."""
    out: set[str] = set()
    for sub in ast.walk(stmt):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.lower().endswith("row"):
                    out.add(target.id)
    return out


def _rows_fed_selects_outside_loops(file_name: str, func: ast.AST) -> list[str]:
    """Flag rows created outside a loop that the loop feeds a select into.

    The sneaky 2026-10-17 Roles shape: the row variable is assigned
    before the loop and ``add_item(<Select>)`` runs inside it — every
    iteration re-feeds the same row object. A row created inside the
    loop is a fresh row per iteration and stays legal.
    """
    found: list[str] = []
    for loop in ast.walk(func):
        if not isinstance(loop, (ast.For, ast.AsyncFor)):
            continue
        created_in_loop: set[str] = set()
        for stmt in loop.body:
            created_in_loop |= _created_row_names(stmt)
        for sub in ast.walk(loop):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            if sub.func.attr != "add_item" or not isinstance(sub.func.value, ast.Name):
                continue
            row_var = sub.func.value.id
            if row_var in created_in_loop:
                continue
            if "Select" in _select_name(sub):
                found.append(
                    f"{file_name}: row {row_var} created outside the loop, select added inside"
                )
    return found


def _rows_fed_two_selects(file_name: str, func: ast.AST) -> list[str]:
    """Flag row variables with more than one select call site."""
    select_counts: dict[str, int] = {}
    for sub in ast.walk(func):
        if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
            continue
        if sub.func.attr != "add_item" or not isinstance(sub.func.value, ast.Name):
            continue
        row_var = sub.func.value.id
        if not row_var.lower().endswith("row"):
            continue
        if "Select" in _select_name(sub):
            select_counts[row_var] = select_counts.get(row_var, 0) + 1
    return [
        f"{file_name}: {row_var} fed {count} selects"
        for row_var, count in select_counts.items()
        if count > 1
    ]


def test_action_rows_hold_one_select() -> None:
    """An ActionRow never carries two selects (Discord's row budget).

    The 2026-10-17 Roles bug: two FunctionRolesSelect stacked in one
    ActionRow raised ``maximum number of children exceeded`` at render
    time — a select occupies a whole row, one row per select. Two
    static shapes are fatal:

    - two ``add_item(<...Select>)`` call sites on the same row variable;
    - a row created **outside** a loop while a select is added to it
      **inside** the loop (the loop re-feeds the same row object —
      the exact Roles bug, invisible to a naive call-site count).
    """
    offenders: list[str] = []

    for path in ROOT.glob("src/kingdoms/**/*.py"):
        if "rpc_generated" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            offenders.extend(_rows_fed_two_selects(path.name, func))
            offenders.extend(_rows_fed_selects_outside_loops(path.name, func))
    assert not offenders, (
        "These action rows hold more than one select (maximum children exceeded "
        "at render time — the 2026-10-17 Roles lesson): "
        + "; ".join(sorted(offenders))
    )
