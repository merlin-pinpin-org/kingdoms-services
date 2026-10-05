"""Opt-in debug capture for provider fetches and mapping failures.

Enabled only when ``KINGDOMS_DEBUG`` is set in the environment — never
active in production by default. When enabled, every capture is written
as one JSON line to ``KINGDOMS_DEBUG_FILE`` (default: stderr), so a run
can be replayed and diffed offline:

- raw provider payloads (lobbies, match details, stats);
- enrichment decisions (why a match was skipped or fetched);
- extraction results (what our mapping kept from a payload);
- decode/mapping failures (blob errors, unexpected shapes).

Disabled, every call is a no-op with negligible overhead — the seams
stay in the hot path unconditionally, the cost does not.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
from typing import Any

ENV_FLAG = "KINGDOMS_DEBUG"
ENV_FILE = "KINGDOMS_DEBUG_FILE"


def debug_enabled(environ: dict[str, str] | None = None) -> bool:
    """Return true when debug capture is explicitly enabled."""
    env = os.environ if environ is None else environ
    return env.get(ENV_FLAG, "").strip().lower() in {"1", "true", "yes"}


def capture(kind: str, **fields: Any) -> None:
    """Write one JSON debug line when enabled; no-op otherwise."""
    if not debug_enabled():
        return
    record = {
        "at": datetime.datetime.now(datetime.UTC).isoformat(),
        "kind": kind,
        **fields,
    }
    target = os.environ.get(ENV_FILE, "").strip()
    line = json.dumps(record, default=str, ensure_ascii=False)
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    else:
        print(line, file=sys.stderr)  # noqa: T201 - debug sink


__all__ = ["capture", "debug_enabled"]
