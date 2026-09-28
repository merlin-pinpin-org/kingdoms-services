"""Architectural guard: the 3-second rule (defer before any slow await).

Discord invalidates an interaction ~3s after it fires. A command that
starts its work with a service/database await and answers later gets
`NotFound 10062 (Unknown interaction)` — the user sees "This
interaction failed". The rule (discord-ui skill, kingdoms repo): the
acknowledgment (defer or immediate answer) is the FIRST await on every
command path; slow reads ride the followup afterwards.

This test reads the source of every registered slash command and fails
when a non-interaction await precedes the first acknowledgment. It is
structural on purpose: simcord cannot simulate wall-clock time, and
the property to protect is the code shape, not the timing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# (file, command callback) — every tree-registered slash command.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("src/kingdoms/discord/status.py", "status_command"),
    ("src/kingdoms/discord/admin.py", "admin_command"),
)

ACK_RE = re.compile(r"await interaction\.response\.(defer|send_message|send_modal|edit_message)")
SLOW_RE = re.compile(r"\n\s+await (?!interaction\.response\.)(?!interaction\.followup\.)\w")


def _command_body(path: str, callback: str) -> str:
    """Return the source of one command callback (rough: 2600 chars)."""
    src = (ROOT / path).read_text(encoding="utf-8")
    i = src.find(f"async def {callback}")
    assert i != -1, f"callback {callback} not found in {path}"
    return src[i : i + 2600]


@pytest.mark.parametrize("path,callback", COMMANDS)
def test_every_command_acknowledges_before_any_slow_await(path: str, callback: str) -> None:
    """The first await on a command path is the acknowledgment."""
    body = _command_body(path, callback)
    ack = ACK_RE.search(body)
    slow = SLOW_RE.search(body)
    assert ack is not None, (
        f"{path}:{callback} never acknowledges (defer/send) — a slow read "
        "would answer past Discord's 3s window (10062)"
    )
    if slow is not None:
        assert ack.start() < slow.start(), (
            f"{path}:{callback} awaits a service before acknowledging — "
            "move `interaction.response.defer(...)` to the very first statement "
            "(the 3-second rule, discord-ui skill)"
        )


def test_the_command_list_covers_every_tree_command() -> None:
    """The guard is only as good as its inventory: every @tree.command is listed."""
    registered: set[tuple[str, str]] = set()
    for py in (ROOT / "src").rglob("*.py"):
        src = py.read_text(encoding="utf-8")
        for m in re.finditer(r"@tree\.command|async def (\w+)\(interaction", src):
            if m.group(1):
                rel = str(py.relative_to(ROOT))
                registered.add((rel, m.group(1)))
    for path, callback in COMMANDS:
        assert (path, callback) in registered or _command_body(path, callback), (
            f"stale guard entry: {path}:{callback}"
        )
