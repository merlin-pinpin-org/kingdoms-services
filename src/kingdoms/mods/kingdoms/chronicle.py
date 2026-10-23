"""Kingdoms mod — the Lord chronicle (Seigneurie V2 + corbeau-v3, Drasah 15/10/2026).

Every lord carries a FACTUAL, exactly-timestamped log of the actions
they REALLY CLICKED (Drasah's fundamental rule, 15/10): actions
SUFFERED never appear on the victim's fiche (being attacked shows on
the ATTACKER's chronicle), system events never appear (cycle/age
switches, Lord's Day losses — nobody clicked), and kingdom-level
actions are recorded on the CLICKING LORD, not the kingdom.

The chronicle lives INSIDE the lord document (``LordModel.chronicle``,
cap 50, oldest entries dropped first) — the MemoryStore test base stays
duck-typed and the KingdomsStore Protocol untouched. Recording is
BEST-EFFORT: a chronicle failure must never break the game action it
follows (every call site is post-success).

The GATING lives here too: a fiche shows the status + chronique ONLY
when the target lord is currently « suivi » by the CONSULTING
kingdom's Corbeau (``corbeau_track``, 24h window) — otherwise the fiche
shows the bare identity only.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kingdoms.mods.kingdoms.models import ChronicleEntryModel

if TYPE_CHECKING:  # pragma: no cover — import cycles never at runtime
    from kingdoms.mods.kingdoms.models import KingdomModel, LordModel

logger = logging.getLogger("kingdoms.chronicle")

CHRONICLE_CAP = 50
"""Maximum entries kept per lord (oldest dropped first)."""

# --------------------------------------------------------------------------
# Kinds — one constant per CLICKED action family (Seigneurie V2 inventory)
# --------------------------------------------------------------------------

# Gestion perso
KIND_LEFT = "left"
KIND_CROWNED = "crowned"
KIND_ENROLLED = "enrolled"

# Mariages (civ + tier Paroisse)
KIND_MARRIAGE = "marriage"
KIND_ARRANGED_MARRIAGE = "arranged_marriage"
KIND_ROYAL_MARRIAGE = "royal_marriage"

# Combat
KIND_ATTACK_DECLARED = "attack_declared"
KIND_ATTACK_SCHEDULED = "attack_scheduled"  # planification-v1 (Drasah 23/10)
KIND_EMBUSCADE = "embuscade"
KIND_DEFENSE = "defense"
KIND_PICKBAN = "pickban"
KIND_ATTACK_RESULT = "attack_result"
KIND_DEFENSE_RESULT = "defense_result"
KIND_RESTITUTION = "restitution"

# FFA Exploration
KIND_FFA_JOINED = "ffa_joined"
KIND_FFA_LEFT = "ffa_left"
KIND_FFA_RESULT = "ffa_result"

# Volière (par cliqueur)
KIND_BIRD_CHOSEN = "bird_chosen"
KIND_VOLIERE_ACTION = "voliere_action"
KIND_CORBEAU_THEFT = "corbeau_theft"
KIND_CORBEAU_THEFT_STOP = "corbeau_theft_stop"
KIND_CORBEAU_THEFT_DEAD = "corbeau_theft_dead"
KIND_CORBEAU_CONTESTED = "corbeau_contested"

# Marché / Paroisse / Cathédrale
KIND_MARKET_PURCHASE = "market_purchase"
KIND_PARISH_UPGRADE = "parish_upgrade"
KIND_CATHEDRAL_CONSECRATED = "cathedral_consecrated"


async def record(
    lord: LordModel,
    kind: str,
    *,
    now: datetime,
    store: Any,
    **params: str,
) -> None:
    """Append one chronicle entry to a lord's log (BEST-EFFORT, never raises).

    Called AFTER the game action succeeded: a chronicle failure is
    logged for the admin but must never break the action it records.
    """
    try:
        entries = list(lord.chronicle)
        entries.append(
            ChronicleEntryModel(
                at=int(now.timestamp()),
                kind=str(kind),
                params={str(key): str(value) for key, value in params.items()},
            )
        )
        lord.chronicle = entries[-CHRONICLE_CAP:]
        await store.upsert_lord(lord.to_mongo())
    except Exception:  # best-effort by design (Drasah's ruling)
        logger.exception(
            "kingdoms chronicle: failed to record %s on %s", kind, lord.id
        )


def recent(lord: LordModel, count: int = 3) -> list[ChronicleEntryModel]:
    """Return the ``count`` most recent entries, NEWEST FIRST."""
    entries = list(lord.chronicle)
    entries.reverse()
    return entries[: max(0, int(count))]


def is_tracked_by(kingdom: KingdomModel, lord_id: str, now: datetime) -> bool:
    """Whether ``kingdom`` currently « suit » the lord (corbeau_track, 24h)."""
    timestamp = int(now.timestamp())
    for entry in kingdom.corbeau_tracking or []:
        if (
            str(entry.get("lord_id")) == str(lord_id)
            and int(entry.get("until", 0) or 0) > timestamp
        ):
            return True
    return False
