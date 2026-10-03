"""Vibe session fix loop (v6).

Residual mypy fix in channels_platform.py: _channels_of_kind()
is typed "list[object]", so ".id" access fails mypy strict.
Type it (and the "found" accumulator) as ForumChannel/TextChannel
so attribute access is valid. Plain typing edits: ruff --fix
leaves them untouched (unlike getattr() rewrites, which ruff
reverted in the previous run).
"""
from __future__ import annotations

from pathlib import Path

PATH = Path("src/kingdoms/discord/channels_platform.py")

UNION = "discord.ForumChannel | discord.TextChannel"

EDITS: list[tuple[str, str]] = [
    (
        "def _channels_of_kind(self, guild: discord.Guild, kind: str) -> list[object]:",
        "def _channels_of_kind(self, guild: discord.Guild, kind: str) -> list[" + UNION + "]:",
    ),
    (
        "return list(guild.text_channels)",
        "return list[" + UNION + "](guild.text_channels)",
    ),
    (
        "found: object | None = None",
        "found: " + UNION + " | None = None",
    ),
]


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    for old, new in EDITS:
        if new in text:
            print("already applied:", new)
            continue
        if old not in text:
            print("MISSING PATTERN (FATAL):", old)
            raise SystemExit(1)
        text = text.replace(old, new, 1)
        print("applied edit:", new)
    PATH.write_text(text, encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
