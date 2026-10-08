"""Kingdoms mod seeding — the seed engine behind the CLI.

Writes the mod's own season data (kingdoms, territories, lords) into
Mongo, keyed by the visible ids: the season id must exist (it comes
from a launch or the core registry — the seed never invents one), and
territories are addressed as ``territory:<season id>:<map key>``.

Idempotent: existing ids skip, so a crashed deployment can just
re-run. The core catalog (maps, pools) is **not** touched — it is the
core AoE2 seed's business; the mod seed only references map keys.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("kingdoms.mods.kingdoms.seed")


@dataclass
class KingdomsSeedReport:
    """The seed's per-section created counts."""

    season_id: str = ""
    kingdoms: int = 0
    territories: int = 0
    lords: int = 0


async def seed_kingdoms_mod(database: Any, data: dict[str, Any], guild_id: str) -> KingdomsSeedReport:
    """Idempotently write the mod's season data; return the counts."""
    from kingdoms.core.ids import territory_id
    from kingdoms.mods.kingdoms.models import KingdomModel, KingdomType, LordModel, LordRole, TerritoryModel

    season_id = str(data.get("season_id") or "").strip()
    if not season_id:
        raise ValueError("kingdoms seed: season_id is required (the footer id of a launched season)")
    report = KingdomsSeedReport(season_id=season_id)
    now = datetime.now(tz=UTC)
    kingdoms_col = database["kingdoms_kingdoms"]
    territories_col = database["kingdoms_territories"]
    lords_col = database["kingdoms_lords"]

    names = [str(n).strip() for n in data.get("kingdoms", []) or [] if str(n).strip()]
    kingdom_ids: dict[str, str] = {}
    for index, name in enumerate(names):
        kid = f"k-{index + 1}"
        kingdom_ids[name] = kid
        if await _has(await kingdoms_col.find_one({"_id": kid})):
            continue
        doc = KingdomModel(
            _id=kid,
            season_id=season_id,
            type=KingdomType.PLAYER,
            name=name,
        ).to_mongo()
        await kingdoms_col.insert_one(doc)
        report.kingdoms += 1

    for spec in data.get("territories", []) or []:
        map_key = str(spec.get("map_key", "")).strip()
        owner = str(spec.get("owner", "gaia")).strip()
        if not map_key:
            raise ValueError("kingdoms seed: a territory needs a map_key")
        tid = territory_id(season_id, map_key)
        if await _has(await territories_col.find_one({"_id": tid})):
            continue
        owner_id = "gaia" if owner == "gaia" else kingdom_ids.get(owner, owner)
        doc = TerritoryModel(
            _id=tid,
            season_id=season_id,
            map_key=map_key,
            owner_kingdom_id=owner_id,
            drawn_at=now,
        ).to_mongo()
        await territories_col.insert_one(doc)
        report.territories += 1

    for spec in data.get("lords", []) or []:
        player_id = str(spec.get("player_id", "")).strip()
        if not player_id:
            raise ValueError("kingdoms seed: a lord needs a player_id (the Discord user id)")
        if await _has(await lords_col.find_one({"_id": player_id})):
            continue
        doc = LordModel(
            _id=player_id,
            season_id=season_id,
            kingdom_id=spec.get("kingdom") or None,
            role=LordRole(str(spec.get("role", "lord"))),
            display_name=str(spec.get("display_name", "")),
        ).to_mongo()
        await lords_col.insert_one(doc)
        report.lords += 1

    logger.info("kingdoms seed: %s", report)
    return report


async def _has(result: Any) -> bool:
    """Whether an awaited find_one returned a document."""
    return result is not None
