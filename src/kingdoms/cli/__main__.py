"""kingdoms — developer CLI entry point.

Dispatches to the subcommand modules (doctor, local, remote, runner).
Kept dependency-free (stdlib argparse) so it works in any checkout.
"""

from __future__ import annotations

import argparse
import os
import sys

from kingdoms.cli import doctor, local, remote, runner


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="kingdoms",
        description="Didactic developer CLI for the Kingdoms platform — local dev, remote ops, CI runner.",
    )
    parser.add_argument("--repo-root", default=None, help="Path to the repository root (default: cwd)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="check your local environment and print fixes")
    sub.add_parser("local", help="manage the local dev stack (up/down/logs/status/reset/test/lint)")
    sub.add_parser("remote", help="manage a remote server over SSH (key auth only)")
    sub.add_parser("runner", help="guided self-hosted GitHub Actions runner setup")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI main; return the process exit code."""
    parser = build_parser()
    args, rest = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    root = args.repo_root or os.getcwd()
    if args.command == "doctor" and not rest:
        return doctor.print_report(doctor.run_checks(root))
    if args.command == "local":
        return local.main(rest)
    if args.command == "remote":
        return remote.main(rest)
    if args.command == "runner":
        return runner.main(rest)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
