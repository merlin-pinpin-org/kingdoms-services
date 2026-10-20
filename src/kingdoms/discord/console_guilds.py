"""Test-guild allowlist: the console's scope, defined by env only.

Developer-mandated rule (the dev console session): the dev console's
commands (clean, nuke, reset db, install) are executable **only on the
guilds listed in the ``KINGDOMS_TEST_GUILD_IDS`` environment variable**
\u2014 never on any other guild, whatever the flags and permissions say.

Design points:

- **Env-defined, runtime-immutable** \u2014 the variable is read once at
  import time (a module-level frozenset); nothing in the Discord
  surface can add, remove or override a test guild id. The compose
  files (kingdoms-infra) set it from the GitHub environment variable
  ``TEST_GUILD_ID``; prod never sets it.
- **Fail closed** \u2014 an unset or unparsable variable yields an empty
  allowlist, and an empty allowlist denies everything (``/dev`` does
  not even register). A test env that forgets to declare its guild
  ids gets no console, never an unrestricted one.
- **Numeric discipline** \u2014 Discord snowflakes are numeric strings;
  anything else is refused at parse time (logged once).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("kingdoms.discord.console_guilds")

__all__ = [
    "TEST_GUILD_IDS_ENV_VAR",
    "allowed_test_guild_ids",
    "parse_test_guild_ids",
]

TEST_GUILD_IDS_ENV_VAR = "KINGDOMS_TEST_GUILD_IDS"


def _parse(environ: dict[str, str] | None = None) -> frozenset[str]:
    """Parse the allowlist from the env, refusing non-numeric entries."""
    env = os.environ if environ is None else environ
    raw = env.get(TEST_GUILD_IDS_ENV_VAR, "").strip()
    if not raw:
        return frozenset()
    ids: set[str] = set()
    for chunk in raw.replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            logger.warning("TEST GUILD allowlist: refusing non-numeric id %r", chunk)
            continue
        ids.add(chunk)
    return frozenset(ids)


_TEST_GUILD_IDS: frozenset[str] = _parse()


def allowed_test_guild_ids() -> frozenset[str]:
    """Return the immutable test-guild allowlist (env-defined).

    The value is computed once at import: no code path can change it at
    runtime. Test it with ``is_test_guild``; never use membership on a
    guild the console did not itself check.
    """
    return _TEST_GUILD_IDS


def is_test_guild(guild_id: str | int | None) -> bool:
    """Whether one guild id is on the env-defined test allowlist."""
    if guild_id is None:
        return False
    return str(guild_id) in _TEST_GUILD_IDS


def parse_test_guild_ids(environ: dict[str, str]) -> frozenset[str]:
    """Parse the allowlist from an explicit environ (tests, tooling)."""
    return _parse(environ)
