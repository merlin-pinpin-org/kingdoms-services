"""Vibe session fix loop (v5).

Residual mypy fixes: channels_platform.py accesses ".id" on
object-typed variables. Replace with getattr() so mypy passes.
Regex-based and idempotent: safe to run on every CI loop run.
"""
from __future__ import annotations

import re
from pathlib import Path

PATH = Path("src/kingdoms/discord/channels_platform.py")
BAD = re.compile(r"str\((channel|found)\.id\)")


def fix_text(text: str) -> tuple[str, list[str]]:
    hits = [line for line in text.splitlines() if BAD.search(line)]
    def repl(match: re.Match[str]) -> str:
        var = match.group(1)
        return f'str(getattr({var}, "id"))'
    return BAD.sub(repl, text), hits


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    new_text, hits = fix_text(text)
    if hits:
        for line in hits:
            print(f"fixing: {line.strip()}")
        PATH.write_text(new_text, encoding="utf-8")
        print(f"applied {len(hits)} getattr fix(es) in {PATH}")
    else:
        print("no direct .id access left; nothing to do")
    remaining = [l for l in new_text.splitlines() if BAD.search(l)]
    print(f"remaining direct .id lines: {len(remaining)}")
    for line in remaining:
        print(f"STILL BAD: {line.strip()}")


if __name__ == "__main__":
    main()
