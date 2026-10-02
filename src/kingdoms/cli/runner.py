"""Guided setup of a self-hosted GitHub Actions runner (optional).

Never downloads or executes anything on its own: this module explains the
steps, checks the local `gh` CLI, and prints the exact commands to run
(kingdoms-services#193).
"""

from __future__ import annotations

import shutil
import sys

SECURITY_NOTES = (
    "Read this first — self-hosted runners execute arbitrary workflow code:",
    " - Use a DEDICATED machine or VM for the runner, never your daily laptop.",
    " - Never expose the runner to the public internet; it polls GitHub over TLS.",
    " - Prefer org-level runners with tight labels, and repo 'Allow select actions'.",
    " - The registration token is short-lived; still, never paste it anywhere else.",
)


def cmd_setup(repo: str | None) -> int:
    """Print the guided setup path; interactive confirmation first."""
    print("kingdoms runner setup — self-hosted GitHub Actions runner\n")
    if input("This guide only prints instructions; nothing is executed. Continue? (yes/no): ").strip().lower() != "yes":
        print("Aborted.")
        return 0
    for line in SECURITY_NOTES:
        print(line)
    print()
    target = repo or "<owner>/<repo>"
    print(f"Target repository: {target}\n")
    if shutil.which("gh") is None:
        print("gh CLI not found — install it: https://cli.github.com/")
        print("Then authenticate: gh auth login")
    else:
        print("gh CLI found. Fastest path (run these yourself):")
        print(f"  gh api /repos/{target}/actions/runners/registration-token --jq .token")
        print("  # then follow the printed download+config.sh instructions from:")
        print(f"  # https://github.com/{target}/settings/actions/runners/new")
    print("\nManual path (no gh): open the URL above in your browser, copy the")
    print("download + config commands, and run them on the dedicated machine.")
    print("\nSanity check once it runs:")
    print(f"  gh api /repos/{target}/actions/runners --jq '.runners[].name'")
    return 0


def main(argv: list[str]) -> int:
    """Dispatch `kingdoms runner <action>`; return exit code."""
    if argv and argv[0] == "setup":
        repo = argv[1] if len(argv) > 1 else None
        return cmd_setup(repo)
    print("Usage: kingdoms runner setup [owner/repo]")
    print("  setup — guided, security-first instructions for a self-hosted runner.")
    return 2 if argv else 0


def _unused() -> None:
    _ = sys.argv
