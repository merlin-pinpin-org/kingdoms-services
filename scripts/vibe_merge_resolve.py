"""Per-path conflict resolution for the stacked branch merge.

Runs while a merge is in conflict (MERGE_HEAD present). Files whose
final content is the refined salons-first implementation (fixed on
the base branch through kingdoms-services#175) resolve to the base
side; everything else keeps this branch's feature work.
"""
from __future__ import annotations

import subprocess

TAKE_BASE = {
    "src/kingdoms/core/services/channel.py",
    "src/kingdoms/discord/channels_platform.py",
    "tests/mocks/provision.py",
    "tests/unit/test_core/test_channel_service.py",
}


def run(args: list[str]) -> str:
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        print("command failed:", " ".join(args), proc.stderr.strip())
    return proc.stdout


def main() -> None:
    conflicted = [f for f in run(["git", "diff", "--name-only", "--diff-filter=U"]).splitlines() if f]
    if not conflicted:
        print("no conflicts to resolve")
        return
    for path in conflicted:
        side = "--theirs" if path in TAKE_BASE else "--ours"
        run(["git", "checkout", side, "--", path])
        run(["git", "add", "--", path])
        print("resolved", path, "with", side)
    remaining = [f for f in run(["git", "diff", "--name-only", "--diff-filter=U"]).splitlines() if f]
    print("remaining unmerged:", remaining)


if __name__ == "__main__":
    main()
