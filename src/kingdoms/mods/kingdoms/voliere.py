"""Kingdoms mod — the Volière service (voliere-v1, Drasah 14/10/2026).

One volière per kingdom, developed for free from the Âge Féodal: three
birds with per-action cooldowns SHARED by the whole kingdom (any lord
may act, the cooldown gates the kingdom).

- **Corbeau** : piocher une civilisation (168h), vérifier une
  déclaration d'attaque (24h — the Corbeau ALWAYS pierces the
  contre-espionnage cover), cibler un seigneur (48h — « suivi » :
  every attack the lord declares leaves with the 24h delay for 24h).
- **Faucon** : piocher un territoire among the not-yet-drawn maps
  (96h), sabotage en rencontre counting in the pick & ban 2-limit
  (48h), Lâcher de faucon (168h — 24h of immunity to every adverse
  technology effect).
- **Pigeon** : piocher une civilisation (72h), question anonyme (1h),
  MP anonyme à un royaume (1h). The traité secret is PARKED until
  Drasah specifies the treaty types.

Anonymity is player-facing only — every action logs the acting lord
for the admin (anti-abus, arbitrage Drasah).
"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any

from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
from kingdoms.mods.kingdoms.models import KingdomModel
from kingdoms.mods.kingdoms.service import KingdomsModError, KingdomsService
from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.voliere")

FEUDAL_AGE_KEY = "feudal_age"
"""The volière unlocks at the Âge Féodal (voliere-v1)."""

CORBEAU_ACTIONS = ("corbeau_civ", "corbeau_verify", "corbeau_track")
FAUCON_ACTIONS = ("faucon_territory", "faucon_sabotage", "faucon_release")
PIGEON_ACTIONS = ("pigeon_civ", "pigeon_question", "pigeon_dm")


class VoliereError(KingdomsModError):
    """Base of the volière errors (voliere-v1)."""

    code = "KINGDOMS_VOLIERE_ERROR"
    message_key = "kingdoms.errors.voliere_error"


class VoliereLockedError(VoliereError):
    """Raised when the season is not yet at the Âge Féodal."""

    code = "KINGDOMS_VOLIERE_LOCKED"
    message_key = "kingdoms.errors.voliere_locked"


class VoliereCooldownError(VoliereError):
    """Raised when the kingdom's shared cooldown is still running."""

    code = "KINGDOMS_VOLIERE_COOLDOWN"
    message_key = "kingdoms.errors.voliere_cooldown"


class VolierePoolEmptyError(VoliereError):
    """Raised when the draw pool has nothing left (civs or maps)."""

    code = "KINGDOMS_VOLIERE_POOL_EMPTY"
    message_key = "kingdoms.errors.voliere_pool_empty"


class VoliereTargetError(VoliereError):
    """Raised on an invalid action target (attack, lord, civ)."""

    code = "KINGDOMS_VOLIERE_TARGET"
    message_key = "kingdoms.errors.voliere_target"


class VoliereService:
    """The volière domain logic — cooldowns, draws and effects."""

    def __init__(
        self,
        store: Any,
        config: KingdomsSeasonConfig,
        kingdoms: KingdomsService,
        territories: TerritoryService,
        attacks: Any,
    ) -> None:
        self._store = store
        self._config = config
        self._kingdoms = kingdoms
        self._territories = territories
        self._attacks = attacks
        self._rng = random.Random()  # noqa: S311 - game draw, not crypto

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @property
    def settings(self) -> Any:
        """The volière settings (cooldowns in hours)."""
        return self._config.voliere

    async def _require_kingdom(self, kingdom_id: str) -> KingdomModel:
        kingdoms = await self._kingdoms.kingdoms()
        kingdom = next((k for k in kingdoms if k.id == kingdom_id), None)
        if kingdom is None or getattr(kingdom, "is_gaia", False):
            raise VoliereTargetError("the kingdom does not exist")
        return kingdom

    async def _save(self, kingdom: KingdomModel) -> None:
        await self._store.upsert_kingdom(kingdom.to_mongo())

    def _cooldown_hours(self, action: str) -> int:
        return int(getattr(self.settings, f"{action}_cooldown_hours"))

    def _ready_at(self, kingdom: KingdomModel, action: str) -> int:
        return int(kingdom.voliere_cooldowns.get(action, 0) or 0)

    async def cooldown_ready_at(self, kingdom_id: str, action: str) -> int:
        """Return the unix timestamp the action is ready at (admin/view)."""
        kingdom = await self._require_kingdom(kingdom_id)
        return self._ready_at(kingdom, action)

    def _check_cooldown(self, kingdom: KingdomModel, action: str, now: datetime) -> None:
        ready_at = self._ready_at(kingdom, action)
        if ready_at > int(now.timestamp()):
            raise VoliereCooldownError(
                "the kingdom's volière cooldown is still running",
                action=action,
            )

    async def _spend(self, kingdom: KingdomModel, action: str, now: datetime) -> None:
        self._check_cooldown(kingdom, action, now)
        cooldowns = dict(kingdom.voliere_cooldowns)
        cooldowns[action] = int(now.timestamp()) + self._cooldown_hours(action) * 3600
        kingdom.voliere_cooldowns = cooldowns
        await self._save(kingdom)

    async def _require_feudal(self, now: datetime) -> None:
        """Refuse every volière action before the Âge Féodal."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise VoliereLockedError("no season is running")
        age_keys = [age.key for age in self._config.ages]
        if FEUDAL_AGE_KEY not in age_keys:
            raise VoliereLockedError("the season declares no Âge Féodal")
        if age_keys.index(season.current_age_key) < age_keys.index(FEUDAL_AGE_KEY):
            raise VoliereLockedError("the season is not yet at the Âge Féodal")

    # ------------------------------------------------------------------
    # Draws (Corbeau civ, Faucon territory, Pigeon civ)
    # ------------------------------------------------------------------

    async def _married_civs(self) -> set[str]:
        lords = await self._kingdoms.lords()
        return {
            str(lord.married_civilization)
            for lord in lords
            if lord.married_civilization is not None and not lord.left
        }

    async def pick_civilization(self, kingdom_id: str, action: str, *, now: datetime) -> str:
        """Draw one NEW civilization for the kingdom (Drasah's ruling).

        The draw covers the WHOLE catalog except the kingdom's hand and
        the married civilizations (exclusivité D74 conservée).
        """
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, action, now)
        hand = {str(civ) for civ in kingdom.civilizations}
        married = await self._married_civs()
        pool = [
            civ.key
            for civ in self._config.civilizations
            if civ.key not in hand and civ.key not in married
        ]
        if not pool:
            raise VolierePoolEmptyError("no civilization is drawable")
        drawn = self._rng.choice(pool)
        civilizations = list(kingdom.civilizations)
        civilizations.append(drawn)
        kingdom.civilizations = civilizations
        await self._save(kingdom)
        logger.info("kingdoms volière: %s drew the civilization %s", kingdom_id, drawn)
        return drawn

    async def pick_territory(self, kingdom_id: str, *, now: datetime) -> str:
        """Draw one not-yet-in-game map as a NEW territory for the kingdom."""
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, "faucon_territory", now)
        territories = await self._territories.territories()
        drawn_keys = {str(territory.map_key) for territory in territories}
        pool = [
            entry.key for entry in self._config.maps if entry.key not in drawn_keys
        ]
        if not pool:
            raise VolierePoolEmptyError("no map is drawable")
        map_key = self._rng.choice(pool)
        await self._territories.draw_map_for(kingdom_id, map_key)
        logger.info("kingdoms volière: %s drew the territory %s", kingdom_id, map_key)
        return map_key

    # ------------------------------------------------------------------
    # Corbeau
    # ------------------------------------------------------------------

    async def verify_attack(self, kingdom_id: str, attack_id: str, *, now: datetime) -> str:
        """Reveal the REAL attacker of one attack against the kingdom.

        The Corbeau ALWAYS pierces the contre-espionnage cover (Drasah's
        arbitration): the returned name is the true attacker, cover or
        not. Returns the attacker's display name.
        """
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, "corbeau_verify", now)
        attack = next(
            (a for a in await self._attacks.attacks() if a.id == attack_id), None
        )
        if attack is None or attack.defender_kingdom_id != kingdom_id:
            raise VoliereTargetError("the attack does not target the kingdom")
        lords = await self._kingdoms.lords()
        attacker = next(
            (lord for lord in lords if lord.id == attack.attacker_lord_id), None
        )
        name = str(attacker.display_name) if attacker is not None else str(attack.attacker_lord_id)
        logger.info("kingdoms volière: %s verified the attack %s", kingdom_id, attack_id)
        return name

    async def track_lord(self, kingdom_id: str, target_lord_id: str, *, now: datetime) -> int:
        """Mark one lord « suivi » — 24h of 24h-delayed attack declarations."""
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, "corbeau_track", now)
        lords = await self._kingdoms.lords()
        target = next((lord for lord in lords if lord.id == target_lord_id), None)
        if target is None or target.left or target.kingdom_id == kingdom_id:
            raise VoliereTargetError("the target is not a trackable lord")
        until = int(now.timestamp()) + int(self.settings.corbeau_track_window_hours) * 3600
        tracking = [
            entry
            for entry in kingdom.corbeau_tracking
            if str(entry.get("lord_id")) != target_lord_id
        ]
        tracking.append({"lord_id": target_lord_id, "until": until})
        kingdom.corbeau_tracking = tracking
        await self._save(kingdom)
        logger.info("kingdoms volière: %s tracks the lord %s", kingdom_id, target_lord_id)
        return until

    # ------------------------------------------------------------------
    # Faucon
    # ------------------------------------------------------------------

    async def release_faucon(self, kingdom_id: str, *, now: datetime) -> int:
        """Open the Lâcher de faucon — 24h immune to adverse technologies."""
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, "faucon_release", now)
        until = int(now.timestamp()) + int(self.settings.faucon_release_window_hours) * 3600
        kingdom.faucon_release_until = until
        await self._save(kingdom)
        logger.info("kingdoms volière: %s released the faucon", kingdom_id)
        return until

    async def faucon_sabotage(
        self, kingdom_id: str, attack_id: str, civ_key: str, *, now: datetime
    ) -> None:
        """Place a Faucon sabotage on an ongoing attack (pick & ban phase).

        Same effect as a pick & ban sabotage — it counts in the
        adversary-side 2-limit (no 3rd sabotage, Drasah's ruling).
        """
        await self._require_feudal(now)
        kingdom = await self._require_kingdom(kingdom_id)
        await self._spend(kingdom, "faucon_sabotage", now)
        attack = next(
            (a for a in await self._attacks.attacks() if a.id == attack_id), None
        )
        if attack is None:
            raise VoliereTargetError("the attack does not exist")
        if kingdom_id not in (attack.attacker_kingdom_id, attack.defender_kingdom_id):
            raise VoliereTargetError("the kingdom is not part of the attack")
        pickban = dict(attack.pickban or {})
        if not bool(pickban.get("sent")):
            raise VoliereTargetError("the pick & ban phase is not running")
        if bool(pickban.get("resolved_pickban")):
            raise VoliereTargetError("the pick & ban phase is over")
        side = "attacker" if kingdom_id == attack.attacker_kingdom_id else "defender"
        sabotages = list(pickban.get(f"{side}_sabotages") or [])
        if len(sabotages) >= 2:
            raise VoliereTargetError("the sabotage limit (2) is already reached")
        if civ_key not in sabotages:
            sabotages.append(civ_key)
        pickban[f"{side}_sabotages"] = sabotages
        attack.pickban = pickban
        await self._store.upsert_attack(attack.to_mongo())
        logger.info(
            "kingdoms volière: %s sabotaged %s on the attack %s",
            kingdom_id,
            civ_key,
            attack_id,
        )

    # ------------------------------------------------------------------
    # Effect queries (attacks.py / economy.py hooks)
    # ------------------------------------------------------------------

    async def is_lord_tracked(self, lord_id: str, now: datetime) -> bool:
        """Whether any kingdom currently « suit » this lord."""
        kingdoms = await self._kingdoms.kingdoms()
        timestamp = int(now.timestamp())
        for kingdom in kingdoms:
            for entry in kingdom.corbeau_tracking or []:
                if str(entry.get("lord_id")) == lord_id and int(entry.get("until", 0) or 0) > timestamp:
                    return True
        return False

    async def has_faucon_release(self, kingdom_id: str, now: datetime) -> bool:
        """Whether the kingdom runs an active Lâcher de faucon."""
        kingdoms = await self._kingdoms.kingdoms()
        kingdom = next((k for k in kingdoms if k.id == kingdom_id), None)
        if kingdom is None:
            return False
        until = int(getattr(kingdom, "faucon_release_until", 0) or 0)
        return until > int(now.timestamp())
