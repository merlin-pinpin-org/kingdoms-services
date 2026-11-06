r"""Console ops CLI: run the /console actions headless, from GitHub Actions.

The Discord surface (``kingdoms.discord.console``) and this CLI are the
**same actions through the same gates**: one capability, two entry
points. The Discord slash command runs interactively behind the
click-time guards; this CLI runs non-interactively (workflow_dispatch
from the PR keywords or the Actions UI) behind the workflow's own gates
(roster matrix on the keyword side, env allowlist here).

Fail-closed invariants (mirrored from the Discord surface, enforced
again at the CLI boundary \\u2014 the workflow is untrusted input):

- the deploy environment must not be prod
  (``KINGDOMS_DEPLOY_ENV``);
- the target guild must be on the env-defined test-guild allowlist
  (``KINGDOMS_TEST_GUILD_IDS``) \\u2014 ``all`` iterates that allowlist and
  nothing else, so a CLI run can never touch an undeclared guild;
- the allowlist is empty \\u2192 the destructive actions refuse to run
  anywhere (fail closed, same rule as the disabled buttons).

Usage (inside the env's kingdoms-core container, by env-console.yml):

    python -m kingdoms.cli.console_ops clean-managed --guild <id|all>
    python -m kingdoms.cli.console_ops nuke            --guild <id|all>
    python -m kingdoms.cli.console_ops reset-data      --guild <id|all>
    python -m kingdoms.cli.console_ops install --guild <id|all> \\
        --mods mod1,mod2 --games game1 [--no-propagate]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("kingdoms.cli.console_ops")

PROD_ENV_NAMES = ("prod", "production")


def _deploy_env() -> str:
    return os.environ.get("KINGDOMS_DEPLOY_ENV", "").strip().lower()


def _fail_closed_gates(guilds: list[str]) -> int:
    """Re-enforce the console gates at the CLI boundary; 0 = proceed."""
    env = _deploy_env()
    if env in PROD_ENV_NAMES:
        print(f"::error::console ops refuse to run on a production environment ({env!r})")
        return 1
    if not guilds:
        print("::error::no target guild: pass --guild <id|all> (never a default)")
        return 1
    from kingdoms.discord.console_guilds import allowed_test_guild_ids, is_test_guild

    allowlist = allowed_test_guild_ids()
    if not allowlist:
        print("::error::KINGDOMS_TEST_GUILD_IDS is empty — no guild may run console ops (fail closed)")
        return 1
    for guild_id in guilds:
        if not is_test_guild(guild_id):
            print(
                f"::error::guild {guild_id} is not on the env allowlist (KINGDOMS_TEST_GUILD_IDS) — refusing"
            )
            return 1
    return 0


def _resolve_targets(guild_arg: str) -> list[str]:
    """Resolve the --guild argument: one id, or the whole allowlist for all."""
    from kingdoms.discord.console_guilds import allowed_test_guild_ids

    if guild_arg == "all":
        return sorted(allowed_test_guild_ids())
    return [guild_arg]


def _parse_list(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


async def _run(action: str, guilds: list[str], mods: list[str], games: list[str], propagate: bool) -> int:
    """Run one action against each target guild through a real Discord client."""
    import discord

    from kingdoms.discord.console import (
        _delete_managed_for_guild,
        _nuke_everything_for_guild,
        _reset_guild_data,
    )
    from kingdoms.discord.console_ops_runner import apply_install_for_guild

    token = os.environ.get("DISCORD_TOKEN", "")
    if not token:
        print("::error::DISCORD_TOKEN is required (the CLI drives a real Discord client)")
        return 1

    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    client = discord.Client(intents=intents)

    async with client:
        await client.login(token)
        await client.wait_until_ready()
        exit_code = 0
        for guild_id in guilds:
            guild = client.get_guild(int(guild_id))
            if guild is None:
                print(f"::error::guild {guild_id} not visible to this bot token")
                exit_code = 1
                continue
            print(f"=== console {action} on guild {guild_id} ({guild.name}) ===")
            try:
                if action == "clean-managed":
                    channels, roles = await _delete_managed_for_guild(guild)
                    print(f"clean-managed done: {channels} channel(s), {roles} role(s) deleted")
                elif action == "nuke":
                    channels, roles = await _nuke_everything_for_guild(guild)
                    print(f"nuke done: {channels} channel(s), {roles} role(s) deleted")
                elif action == "reset-data":
                    deleted = await _reset_guild_data(guild_id)
                    for name, count in sorted(deleted.items()):
                        print(f"reset-data {name}: {count} document(s)")
                elif action == "install":
                    summary = await apply_install_for_guild(guild, mods, games, propagate)
                    print(summary)
            except Exception:
                logger.exception("console %s failed on guild %s", action, guild_id)
                exit_code = 1
        return exit_code


def main(argv: list[str] | None = None) -> int:
    """CLI main; return the process exit code."""
    parser = argparse.ArgumentParser(
        prog="kingdoms console-ops",
        description="Run the /console actions headless against the env's test guilds (fail-closed).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name, help_text in [
        ("clean-managed", "delete only what the bot created (channels, mod roles, bot-admins role)"),
        ("nuke", "delete every channel, category and role of the target guild(s)"),
        ("reset-data", "wipe the target guild(s) persisted data — isolated, nothing global"),
        ("install", "set the mods/games grants, clean uninstalled, provision installed"),
    ]:
        action_parser = sub.add_parser(name, help=help_text)
        action_parser.add_argument(
            "--guild", required=True, help="target guild id, or 'all' for every allowlisted guild"
        )
        if name == "install":
            action_parser.add_argument("--mods", default="", help="comma-separated mod ids to install")
            action_parser.add_argument("--games", default="", help="comma-separated game keys to install")
            action_parser.add_argument(
                "--no-propagate",
                action="store_true",
                help="skip the mod→game / game→mod propagation rules",
            )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    guilds = _resolve_targets(args.guild)
    if _fail_closed_gates(guilds):
        return 1
    mods = _parse_list(getattr(args, "mods", ""))
    games = _parse_list(getattr(args, "games", ""))
    return asyncio.run(_run(args.action, guilds, mods, games, not getattr(args, "no_propagate", False)))


if __name__ == "__main__":
    raise SystemExit(main())
