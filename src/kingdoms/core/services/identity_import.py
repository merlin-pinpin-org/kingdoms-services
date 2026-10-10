"""Identity import: one-shot users.csv load into the core profile bindings.

Legacy association dumps (Discord id + game profile id) describe
*identities*, not ladder membership: the import writes the core
``profile_bindings`` collection (reference §0/§6 — never ladder
collections). Ladder membership stays the ladder mod's business
(`/ladder join`), which reads the bindings through
``RegistrationService.has_any_profile``.

Imported bindings are trusted data (they come from the legacy export),
so validation is not re-run through the game seam; the ``bound_at``
timestamp is the import date, not the legacy association date.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingdoms.core.services.registration import PROFILE_BINDINGS_COLLECTION

DEFAULT_GAME_KEY = "aoe2"


@dataclass(frozen=True, slots=True)
class IdentityUser:
    """One association row: a Discord id with one game profile link."""

    discord_id: str
    display_name: str
    profile_id: str


@dataclass(frozen=True, slots=True)
class IdentityImportReport:
    """Counts of the idempotent identity import, printed by the CLI."""

    users: int
    bindings: int
    rebounds: int


def load_identity_users(path: Path) -> list[IdentityUser]:
    """Load the association CSV, skipping rows without a Discord id."""
    users: list[IdentityUser] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            discord_id = (row.get("discord_id") or "").strip()
            if not discord_id:
                continue
            users.append(
                IdentityUser(
                    discord_id=discord_id,
                    display_name=(row.get("display_name") or "").strip(),
                    profile_id=(row.get("profile_id") or "").strip(),
                )
            )
    return users


async def import_identity_links(
    database: Any,
    users_path: Path,
    game_key: str = DEFAULT_GAME_KEY,
    now_ms: int | None = None,
) -> IdentityImportReport:
    """Import the association CSV into the core profile bindings (idempotent).

    Each (Discord id, profile id) pair becomes one binding document, the
    same shape ``RegistrationService.bind_profile`` writes. Users are
    common to every guild and identities are global: one game profile is
    bound to at most one Discord account at a time, and **the last
    import wins** — a profile found on another account is rebound to
    the importing one (the stale binding is deleted). An existing
    identical binding is left untouched (idempotence).
    """
    users = load_identity_users(users_path)
    bound_at = now_ms if now_ms is not None else int(time.time() * 1000)

    links: dict[tuple[str, str], str] = {}
    for user in users:
        if not user.profile_id:
            continue
        links.setdefault((user.discord_id, user.profile_id), user.display_name)

    collection = database[PROFILE_BINDINGS_COLLECTION]
    bindings = 0
    rebounds = 0
    for (discord_id, profile_id), display_name in links.items():
        entry_id = f"binding:{game_key}:{discord_id}:{profile_id}"
        existing = await collection.find_one({"_id": entry_id})
        if existing is None:
            owner = await collection.find_one({"game_key": game_key, "profile_id": profile_id})
            if owner is not None and owner.get("user_id") != discord_id:
                await collection.delete_one({"_id": owner["_id"]})
                rebounds += 1
        if existing is not None and existing.get("bound_at") == bound_at:
            continue
        binding = {
            "_id": entry_id,
            "user_id": discord_id,
            "game_key": game_key,
            "profile_id": profile_id,
            "profile": {"profile_id": profile_id, "display_name": display_name},
            "bound_at": bound_at,
            "imported": True,
        }
        await collection.replace_one({"_id": entry_id}, binding, upsert=True)
        bindings += 1
    return IdentityImportReport(
        users=len({user.discord_id for user in users}),
        bindings=bindings,
        rebounds=rebounds,
    )
