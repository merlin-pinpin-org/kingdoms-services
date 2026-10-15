"""The /kingdoms-admin debug subgroup: the admin-debug-v1 test toolkit.

Drasah's testing surface (spec admin-debug-v1, 15/10/2026): surgical
mutations nothing a player click does — reset a volière cooldown to
the minute, bring a flown/dead bird back, purge the Explorateur/no-show/
marriage locks, force one attack to its next phase, credit ⚙️, hand a
territory or a civilization, inject chronicle entries, and fast-
forward the corbeau-v3 theft clock (the 1h send delay and the 5-minute
contest window) — plus the ADMIN BYPASS, a process-wide flag that lets
the REAL player buttons run their full normal flow past the gate
checks (age, slot, cooldown, budget, locks, parish tier).

Everything is ephemeral and LOGGED; every mutation names who did what.
The Machine à temps stays the GLOBAL time-jump tool — this panel only
does targeted, single-document mutations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from kingdoms.mods.kingdoms import chronicle
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.models import AttackState, LordModel
from kingdoms.mods.kingdoms.service import KingdomsModError

if TYPE_CHECKING:  # pragma: no cover — import cycles never at runtime
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import KingdomModel
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.admin_debug")

BIRDS = ("corbeau", "faucon", "pigeon")
LOCK_KINDS = ("marriage", "exploration", "ffa")
THEFT_STAGES = ("execute", "window")
ATTACK_STAGES = ("reveal", "pickban", "expire")

# ----------------------------------------------------------------------
# The ADMIN BYPASS — a process-wide flag (default off, config override)
# ----------------------------------------------------------------------

_BYPASS = False


def admin_bypass_enabled() -> bool:
    """Whether the player-button gate checks are bypassed (test mode)."""
    return _BYPASS


def set_admin_bypass(enabled: bool, *, source: str = "runtime") -> None:
    """Flip the ADMIN BYPASS flag (logged — test vs production marker)."""
    global _BYPASS
    if _BYPASS != bool(enabled):
        logger.warning(
            "kingdoms ADMIN BYPASS turned %s (%s source) — gate checks "
            "of the player buttons are bypassed while this is ON",
            "ON" if enabled else "OFF",
            source,
        )
    _BYPASS = bool(enabled)


def bypass_guard(label: str) -> bool:
    """Log one ADMIN BYPASS when a gate check is being skipped.

    Returns True when the caller must SKIP its raise (the bypass is
    active): every skipped gate names itself in the logs — the test
    sessions stay auditable.
    """
    if not _BYPASS:
        return False
    logger.warning("kingdoms ADMIN BYPASS: gate skipped — %s", label)
    return True


class DebugError(KingdomsModError):
    """Raised on an invalid debug mutation (unknown target, bad stage)."""

    code = "KINGDOMS_DEBUG_ERROR"
    message_key = "kingdoms.errors.debug_error"


# ----------------------------------------------------------------------
# The debug service — surgical mutations, each one logged
# ----------------------------------------------------------------------


class DebugService:
    """The targeted-mutation toolkit behind /kingdoms-admin debug."""

    def __init__(
        self,
        store: Any,
        config: KingdomsSeasonConfig,
        kingdoms: KingdomsService,
        territories: TerritoryService,
        attacks: Any,
        voliere: Any,
        *,
        time_machine: Any = None,
    ) -> None:
        self._store = store
        self._config = config
        self._kingdoms = kingdoms
        self._territories = territories
        self._attacks = attacks
        self._voliere = voliere
        # admin-console-v1: the console's `skippe` rides the Machine à
        # temps — optional so the existing wiring (and the tests) keep
        # the six-argument construction.
        self._time_machine = time_machine

    # -- lookups --------------------------------------------------------

    async def _kingdom(self, name_or_id: str) -> KingdomModel:
        """Resolve a kingdom by display name (casefold) or id."""
        kingdoms = await self._kingdoms.kingdoms()
        target = str(name_or_id).strip()
        found = next(
            (k for k in kingdoms if k.id == target or k.name.casefold() == target.casefold()),
            None,
        )
        if found is None:
            raise DebugError(f"unknown kingdom: {name_or_id}")
        return found

    async def _lord(self, name_or_id: str) -> LordModel:
        """Resolve an active lord by id or display name (casefold)."""
        target = str(name_or_id).strip()
        found = next(
            (
                lord
                for lord in await self._kingdoms.lords()
                if lord.id == target or str(lord.display_name).casefold() == target.casefold()
            ),
            None,
        )
        if found is None or found.left:
            raise DebugError(f"unknown lord: {name_or_id}")
        return found

    async def _save_kingdom(self, kingdom: KingdomModel) -> None:
        await self._store.upsert_kingdom(kingdom.to_mongo())

    async def _save_lord(self, lord: LordModel) -> None:
        await self._store.upsert_lord(lord.to_mongo())

    # -- volière ---------------------------------------------------------

    async def reset_voliere_cooldown(
        self, kingdom_ref: str, bird: str, ready_in_minutes: int = 0, *, now: datetime | None = None
    ) -> dict[str, Any]:
        """Land one bird NOW (or ``ready_in_minutes`` minutes from now)."""
        timestamp = int((now or game_now()).timestamp()) + max(0, int(ready_in_minutes)) * 60
        kingdom = await self._kingdom(kingdom_ref)
        if bird not in BIRDS and bird != "all":
            raise DebugError(f"unknown bird: {bird}")
        cooldowns = dict(kingdom.voliere_cooldowns)
        for key in BIRDS if bird == "all" else (bird,):
            cooldowns[key] = timestamp
        kingdom.voliere_cooldowns = cooldowns
        await self._save_kingdom(kingdom)
        logger.warning(
            "kingdoms DEBUG: volière cooldown of %s (%s) reset to +%s min by admin",
            kingdom.name,
            bird,
            ready_in_minutes,
        )
        return {"kingdom": kingdom.name, "bird": bird, "ready_at": timestamp}

    async def grant_bird(self, kingdom_ref: str, bird: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Put a bird back on its perch (slot restored, cooldown landed).

        Restores the age SLOT of a flown or dead bird — the corbeau
        killed by a contest comes back through this (the slot purge is
        the production rule; the debug panel un-does it for testing).
        """
        kingdom = await self._kingdom(kingdom_ref)
        if bird not in BIRDS:
            raise DebugError(f"unknown bird: {bird}")
        slots = [str(entry) for entry in (kingdom.voliere_slots or [])]
        if bird not in slots:
            slots.append(bird)
        kingdom.voliere_slots = slots
        cooldowns = dict(kingdom.voliere_cooldowns)
        cooldowns[bird] = int((now or game_now()).timestamp())
        kingdom.voliere_cooldowns = cooldowns
        await self._save_kingdom(kingdom)
        logger.warning(
            "kingdoms DEBUG: bird %s granted/restored to %s by admin",
            bird,
            kingdom.name,
        )
        return {"kingdom": kingdom.name, "bird": bird, "slots": slots}

    # -- verrous ----------------------------------------------------------

    async def clear_lock(self, lord_ref: str, lock: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Purge one real-time lock of a lord (Explorateur/no-show/marriage)."""
        del now
        lord = await self._lord(lord_ref)
        if lock not in LOCK_KINDS and lock != "all":
            raise DebugError(f"unknown lock: {lock}")
        cleared: list[str] = []
        if lock in ("marriage", "all") and lord.marriage_locked_until is not None:
            lord.marriage_locked_until = None
            cleared.append("marriage")
        if lock in ("exploration", "all") and lord.exploration_locked_until is not None:
            lord.exploration_locked_until = None
            cleared.append("exploration")
        if lock in ("ffa", "all") and lord.ffa_locked_until is not None:
            lord.ffa_locked_until = None
            cleared.append("ffa")
        await self._save_lord(lord)
        logger.warning(
            "kingdoms DEBUG: locks %s purged on %s by admin",
            cleared,
            lord.display_name,
        )
        return {"lord": lord.display_name, "cleared": cleared or ["none-set"]}

    # -- attaques -----------------------------------------------------------

    async def advance_attack(self, attack_id: str, stage: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Force one running attack to its next D77 phase.

        ``reveal`` pulls the game start so the T-15 defense cutoff and
        the cover unmask are due NOW; ``pickban`` pulls it further so
        the T-5 pick & ban DMs are due; ``expire`` closes the game
        moment and runs the no-show expiry.
        """
        moment = now or game_now()
        attacks = await self._attacks.attacks()
        attack = next((a for a in attacks if a.id == str(attack_id)), None)
        if attack is None:
            raise DebugError(f"unknown attack: {attack_id}")
        if attack.is_over:
            raise DebugError(f"the attack {attack_id} is already over")
        if stage not in ATTACK_STAGES:
            raise DebugError(f"unknown stage: {stage}")
        settings = self._config.attacks
        if stage == "reveal":
            attack.expires_at = moment + timedelta(minutes=settings.defense_cutoff_minutes)
        elif stage == "pickban":
            attack.expires_at = moment + timedelta(minutes=settings.pick_ban_lead_minutes)
        else:  # expire
            attack.expires_at = moment
        await self._store.upsert_attack(attack.to_mongo())
        moved = await self._run_attack_due(stage, moment)
        logger.warning(
            "kingdoms DEBUG: attack %s advanced to %s by admin (%s ran)",
            attack_id,
            stage,
            ",".join(moved) or "nothing",
        )
        return {"attack": attack_id, "stage": stage, "ran": moved or ["nothing"]}

    async def _run_attack_due(self, stage: str, moment: datetime) -> list[str]:
        """Run the due phases the forced stage unlocks."""
        moved: list[str] = []
        if stage == "reveal":
            if await self._attacks.reveal_due(now=moment):
                moved.append("reveal")
        elif stage == "pickban":
            if await self._attacks.reveal_due(now=moment):
                moved.append("reveal")
            if await self._attacks.pick_ban_due(now=moment):
                moved.append("pickban")
        else:
            if await self._attacks.expire_stale(now=moment):
                moved.append("expire")
        return moved

    # -- banque / territoires / diplomatie -------------------------------------

    async def adjust_bank(self, kingdom_ref: str, delta: int) -> dict[str, Any]:
        """Credit (or debit, negative) ⚙️ on a kingdom's tech bank."""
        kingdom = await self._kingdom(kingdom_ref)
        before = int(kingdom.tech_points_bank)
        kingdom.tech_points_bank = max(0, before + int(delta))
        await self._save_kingdom(kingdom)
        logger.warning(
            "kingdoms DEBUG: bank of %s %s%+d ⚙️ by admin (%s → %s)",
            kingdom.name,
            "credited" if delta >= 0 else "debited",
            delta,
            before,
            kingdom.tech_points_bank,
        )
        return {"kingdom": kingdom.name, "before": before, "after": kingdom.tech_points_bank}

    async def grant_territory(self, map_key: str, kingdom_ref: str) -> dict[str, Any]:
        """Hand one territory to a kingdom (direct owner transfer)."""
        kingdom = await self._kingdom(kingdom_ref)
        territory = next(
            (t for t in await self._territories.territories() if t.map_key == str(map_key)),
            None,
        )
        if territory is None:
            raise DebugError(f"unknown territory: {map_key}")
        previous = territory.owner_kingdom_id
        territory.owner_kingdom_id = kingdom.id
        await self._store.upsert_territory(territory.to_mongo())
        logger.warning(
            "kingdoms DEBUG: territory %s transferred %s → %s by admin",
            territory.map_key,
            previous,
            kingdom.name,
        )
        return {"map_key": territory.map_key, "from": previous, "to": kingdom.name}

    async def grant_civ(self, kingdom_ref: str, civ: str) -> dict[str, Any]:
        """Force a civilization into a kingdom's hand (diplomacy tests)."""
        kingdom = await self._kingdom(kingdom_ref)
        hand = [str(entry) for entry in kingdom.civilizations]
        if str(civ) not in hand:
            hand.append(str(civ))
            kingdom.civilizations = hand
            await self._save_kingdom(kingdom)
        logger.warning(
            "kingdoms DEBUG: civ %s granted to %s by admin",
            civ,
            kingdom.name,
        )
        return {"kingdom": kingdom.name, "civ": str(civ), "hand": hand}

    # -- seigneurie ---------------------------------------------------------

    async def inject_chronicle(
        self,
        lord_ref: str,
        kind: str,
        params: dict[str, str],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Append one FACTICE chronicle entry on a lord (display tests)."""
        lord = await self._lord(lord_ref)
        await chronicle.record(
            lord,
            str(kind),
            now=now or game_now(),
            store=self._store,
            **{str(key): str(value) for key, value in params.items()},
        )
        fresh = await self._lord(lord_ref)
        logger.warning(
            "kingdoms DEBUG: chronicle entry %s injected on %s by admin",
            kind,
            fresh.display_name,
        )
        return {"lord": fresh.display_name, "kind": str(kind), "entries": len(fresh.chronicle)}

    # -- corbeau v3 ------------------------------------------------------------

    async def advance_theft(self, kingdom_ref: str, stage: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Fast-forward the corbeau-v3 theft clock of one kingdom.

        ``execute`` moves the 1h send delay to NOW and runs the due
        resolution (1st ⚙️ stolen, alarm + contest window open);
        ``window`` expires the 5-minute contest window (the pending
        STOP/CONTINUE settles on its own).
        """
        moment = now or game_now()
        kingdom = await self._kingdom(kingdom_ref)
        if stage not in THEFT_STAGES:
            raise DebugError(f"unknown stage: {stage}")
        theft = dict(kingdom.corbeau_theft or {})
        if not theft:
            raise DebugError(f"{kingdom.name} runs no corbeau theft")
        state = str(theft.get("state"))
        if stage == "execute":
            if state != "pending":
                raise DebugError(f"the theft is not pending (state {state})")
            theft["execute_at"] = int(moment.timestamp())
        else:  # window
            if state not in ("awaiting_continue", "continuing"):
                raise DebugError(f"no contest window to expire (state {state})")
            theft["contest_until"] = int(moment.timestamp())
        kingdom.corbeau_theft = theft
        await self._save_kingdom(kingdom)
        executed = await self._voliere.resolve_due_thefts(now=moment)
        logger.warning(
            "kingdoms DEBUG: corbeau theft of %s advanced to %s by admin (executed: %s)",
            kingdom.name,
            stage,
            [str(t.get("id")) for t in executed] or "nothing",
        )
        return {"kingdom": kingdom.name, "stage": stage, "executed": len(executed)}

    # -- fiches / lectures (admin-console-v1) --------------------------------

    async def view_kingdom(self, kingdom_ref: str) -> dict[str, Any]:
        """Return the full kingdom fiche: bank, birds, locks window, tier, civs."""
        kingdom = await self._kingdom(kingdom_ref)
        return {
            "kind": "kingdom",
            "id": kingdom.id,
            "name": kingdom.name,
            "bank": int(kingdom.tech_points_bank),
            "marriage_points": int(kingdom.marriage_points),
            "arranged_stock": int(kingdom.arranged_marriage_stock),
            "parish_tier": int(kingdom.parish_tier),
            "birds": sorted(str(bird) for bird in (kingdom.voliere_slots or [])),
            "cooldowns": dict(kingdom.voliere_cooldowns or {}),
            "traquenard_until": kingdom.traquenard_until,
            "hand": list(kingdom.civilizations),
            "secured": list(kingdom.secured_civilizations),
        }

    async def view_lord(self, lord_ref: str) -> dict[str, Any]:
        """Return the lord fiche: budgets, marriage, real-time locks, chronicle."""
        lord = await self._lord(lord_ref)
        kingdom = next(
            (k for k in await self._kingdoms.kingdoms() if k.id == lord.kingdom_id),
            None,
        )
        return {
            "kind": "lord",
            "id": lord.id,
            "name": lord.display_name,
            "kingdom": kingdom.name if kingdom is not None else "—",
            "role": str(lord.role),
            "attack_used": int(lord.attack_used),
            "defense_used": int(lord.defense_used),
            "married_civilization": lord.married_civilization,
            "marriage_locked_until": lord.marriage_locked_until,
            "exploration_locked_until": lord.exploration_locked_until,
            "ffa_locked_until": lord.ffa_locked_until,
            "chronicle": len(lord.chronicle),
        }

    async def view_cooldowns(self, kingdom_ref: str | None = None, *, now: datetime | None = None) -> dict[str, Any]:
        """Read the aggregated CD panel: birds, locks and running attacks."""
        moment = now or game_now()
        timestamp = int(moment.timestamp())
        kingdoms = [await self._kingdom(kingdom_ref)] if kingdom_ref else await self._kingdoms.kingdoms()
        lords = await self._kingdoms.lords()
        attacks = await self._attacks.attacks()
        panels: list[dict[str, Any]] = []
        for kingdom in kingdoms:
            birds = {
                str(bird): max(0, int(ready) - timestamp)
                for bird, ready in dict(kingdom.voliere_cooldowns or {}).items()
                if int(ready) > timestamp
            }
            locks = [
                {
                    "lord": lord.display_name,
                    "lock": lock,
                    "left": max(0, int(until) - timestamp),
                }
                for lord in lords
                if lord.kingdom_id == kingdom.id and not lord.left
                for lock, until in (
                    ("marriage", lord.marriage_locked_until),
                    ("exploration", lord.exploration_locked_until),
                    ("ffa", lord.ffa_locked_until),
                )
                if until is not None and int(until) > timestamp
            ]
            traquenard = kingdom.traquenard_until
            running = [
                {
                    "id": attack.id,
                    "map": attack.map_key,
                    "state": str(attack.state),
                    "left": max(0, int((attack.expires_at - moment).total_seconds())),
                }
                for attack in attacks
                if not attack.is_over and kingdom.id in (attack.attacker_kingdom_id, attack.defender_kingdom_id)
            ]
            panels.append(
                {
                    "kingdom": kingdom.name,
                    "birds": birds,
                    "locks": locks,
                    "traquenard_left": max(0, int(traquenard) - timestamp) if traquenard else 0,
                    "attacks": running,
                }
            )
        return {"kingdoms": panels, "now": moment}

    async def view_theft(self, kingdom_ref: str) -> dict[str, Any]:
        """Return the corbeau theft state of one kingdom (phase, stolen, window)."""
        kingdom = await self._kingdom(kingdom_ref)
        theft = dict(kingdom.corbeau_theft or {})
        if not theft:
            raise DebugError(f"{kingdom.name} runs no corbeau theft")
        return {"kingdom": kingdom.name, **theft}

    # -- attaques (console) ---------------------------------------------------

    async def list_attacks(self) -> list[dict[str, Any]]:
        """Every ONGOING attack: id, phase, deadline (the id source)."""
        running: list[dict[str, Any]] = []
        for attack in await self._attacks.attacks():
            if attack.is_over:
                continue
            running.append(
                {
                    "id": attack.id,
                    "map": attack.map_key,
                    "state": str(attack.state),
                    "expires_at": attack.expires_at,
                }
            )
        return running

    async def force_next_phase(self, attack_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Advance ONE running attack by exactly ONE cran (fin_attaque).

        The console's repeated `fin_attaque`: DECLARED → reveal, DEFENDED
        → pick&ban → meeting/score phase → the T+5 no-show line. Each
        call inspects the current state and forces the NEXT step only.
        """
        moment = now or game_now()
        attacks = await self._attacks.attacks()
        attack = next((a for a in attacks if a.id == str(attack_id)), None)
        if attack is None:
            raise DebugError(f"unknown attack: {attack_id}")
        if attack.is_over:
            raise DebugError(f"the attack {attack_id} is already over")
        # 1-2: DECLARED or unrevealed DEFENDED → pull the reveal.
        if attack.state is AttackState.DECLARED or (attack.state is AttackState.DEFENDED and not attack.revealed):
            return await self.advance_attack(attack_id, "reveal", now=moment)
        # 3: DEFENDED, reveal done, pick&ban DMs not due yet → pull them.
        if attack.state is AttackState.DEFENDED and not attack.pickban.get("sent"):
            return await self.advance_attack(attack_id, "pickban", now=moment)
        # 4: pick&ban open but un-resolved → the T meeting moment (forfeit
        # line, or the game-decides wait).
        if attack.state is AttackState.DEFENDED and not attack.pickban.get("resolved_pickban"):
            return await self.advance_attack(attack_id, "expire", now=moment)
        # 5: pick&ban resolved, score phase not opened → open the MEETING.
        if attack.state is AttackState.DEFENDED:
            attack.expires_at = moment
            await self._store.upsert_attack(attack.to_mongo())
            opened = await self._attacks.score_due(now=moment)
            logger.warning(
                "kingdoms DEBUG: attack %s forced to the score phase by admin",
                attack_id,
            )
            return {
                "attack": attack_id,
                "stage": "score",
                "ran": [a.map_key for a in opened] or ["nothing"],
            }
        # 6: MEETING → the T+5 no-show line, now.
        if attack.state is AttackState.MEETING:
            score = dict(attack.score)
            score["ready_deadline"] = int(moment.timestamp()) - 1
            attack.score = score
            await self._store.upsert_attack(attack.to_mongo())
            handled = await self._attacks.ready_due(now=moment)
            logger.warning(
                "kingdoms DEBUG: attack %s forced past the no-show line by admin",
                attack_id,
            )
            return {
                "attack": attack_id,
                "stage": "noshow",
                "ran": [a.map_key for a in handled] or ["nothing (both ready — declare the result)"],
            }
        raise DebugError(f"the attack {attack_id} sits in no forcible state")

    async def admin_result(self, attack_id: str, kingdom_ref: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Apply the admin face-check: override a result / arbitrate a dispute.

        ``resultat <id> <royaume>`` — the named kingdom wins. When the
        attack carries no dispute yet, one is opened first so the
        production ``resolve_dispute`` path applies the reversal (the
        chantier limitation included). ``double``/``les deux`` reads as
        the double forfait.
        """
        moment = now or game_now()
        attacks = await self._attacks.attacks()
        attack = next((a for a in attacks if a.id == str(attack_id)), None)
        if attack is None:
            raise DebugError(f"unknown attack: {attack_id}")
        target = str(kingdom_ref).strip().casefold()
        if target in ("double", "les deux", "les2"):
            choice = "both"
        else:
            kingdom = await self._kingdom(kingdom_ref)
            if kingdom.id == attack.attacker_kingdom_id:
                choice = "attacker"
            elif kingdom.id == attack.defender_kingdom_id:
                choice = "defender"
            else:
                raise DebugError(f"{kingdom.name} sits on neither side of {attack_id}")
        if not attack.score.get("opened") and not attack.score.get("disputed"):
            raise DebugError(f"the attack {attack_id} reached no meeting — use fin_attaque first")
        if not attack.score.get("disputed"):
            score = dict(attack.score)
            score["disputed"] = True
            attack.score = score
            await self._store.upsert_attack(attack.to_mongo())
            logger.warning(
                "kingdoms DEBUG: admin face-check opened the arbitration of %s",
                attack_id,
            )
        resolved = await self._attacks.resolve_dispute(attack_id, choice, now=moment)
        return {
            "attack": attack_id,
            "choice": choice,
            "state": str(resolved.state),
            "winner": resolved.winner_kingdom_id,
        }

    async def restitute_budget(self, lord_ref: str) -> dict[str, Any]:
        """Give one weekly attack budget back to a lord (console D17)."""
        lord = await self._lord(lord_ref)
        before = int(lord.attack_used)
        lord.attack_used = max(0, before - 1)
        await self._save_lord(lord)
        logger.warning(
            "kingdoms DEBUG: attack budget restituted to %s by admin (%s → %s)",
            lord.display_name,
            before,
            lord.attack_used,
        )
        return {"lord": lord.display_name, "before": before, "after": lord.attack_used}

    # -- économie / marché (console) ------------------------------------------

    async def grant_marriages(self, kingdom_ref: str, count: int, civs: list[str] | None = None) -> dict[str, Any]:
        """Stock N marriages on a kingdom's lords (civs optional).

        ``ajouter_mariage`` — the civs in argument first, then the
        kingdom's hand, then the config pool (never a married civ).
        The console tests the marié N fois diplomacy conditions with
        this, without a single player click.
        """
        kingdom = await self._kingdom(kingdom_ref)
        count = int(count)
        if count <= 0:
            raise DebugError("the marriage count must be positive")
        lords = [
            lord
            for lord in await self._kingdoms.lords()
            if lord.kingdom_id == kingdom.id and not lord.left and lord.married_civilization is None
        ]
        if len(lords) < count:
            raise DebugError(f"{kingdom.name} counts only {len(lords)} unmarried active lords")
        taken = {
            str(lord.married_civilization)
            for lord in await self._kingdoms.lords()
            if lord.married_civilization is not None
        }
        pool = [str(civ) for civ in (civs or [])]
        pool += [civ for civ in kingdom.civilizations if civ not in pool]
        pool += [
            civ.key
            for civ in self._config.civilizations
            if civ.key not in pool and civ.key not in kingdom.civilizations
        ]
        granted: dict[str, str] = {}
        for lord in lords[:count]:
            civ = next((entry for entry in pool if entry not in taken and entry not in granted.values()), None)
            if civ is None:
                raise DebugError("no unmarried civilization left in the pool")
            lord.married_civilization = civ
            await self._save_lord(lord)
            granted[lord.display_name] = civ
        logger.warning(
            "kingdoms DEBUG: %d marriage(s) stocked on %s by admin (%s)",
            count,
            kingdom.name,
            granted,
        )
        return {"kingdom": kingdom.name, "granted": granted}

    async def credit_market(self, kingdom_ref: str, entity: str, qty: int) -> dict[str, Any]:
        """Credit a Marché entity on a kingdom (forced purchase, no cost).

        Consumable counters (explorateur, corruption, embuscade) are
        REFUNDED — the per-season consumption goes down, the limit
        opens again. Stocks and charges (garde, traquenard, patrouille)
        are CREDITED — the tokens/slots rise exactly like a paid buy.
        """
        kingdom = await self._kingdom(kingdom_ref)
        entity = str(entity).strip().casefold()
        qty = int(qty)
        state = await self._attacks.technology_state(kingdom.id)
        purchases = dict(state.purchases)
        if entity in ("explorateur", "corruption", "embuscade"):
            purchases[entity] = max(0, int(purchases.get(entity, 0) or 0) - max(1, qty))
        elif entity in ("garde", "garde_royale"):
            purchases["garde_royale_stock"] = int(purchases.get("garde_royale_stock", 0) or 0) + max(1, qty)
        elif entity == "traquenard":
            purchases["traquenard"] = int(purchases.get("traquenard", 0) or 0) + max(1, qty)
        elif entity == "patrouille":
            unlocked = 1
            for slot in (1, 2):
                if unlocked > max(1, qty):
                    break
                purchases[f"patrouille_unlock_{slot}"] = 1
                unlocked += 1
        else:
            raise DebugError(
                "unknown market entity (explorateur, garde, traquenard, patrouille, corruption, embuscade)"
            )
        state.purchases = purchases
        await self._store.upsert_technology(state.to_mongo())
        logger.warning(
            "kingdoms DEBUG: market entity %s of %s credited by admin → %s",
            entity,
            kingdom.name,
            purchases,
        )
        return {"kingdom": kingdom.name, "entity": entity, "purchases": purchases}

    # -- temps / événements (console) -----------------------------------------

    async def skip_to_next_event(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Jump the game clock to the next scheduled passage (`skippe`).

        The earliest of the next cycle switch (JdS), the next age switch
        and the season end — the Machine à temps replays every missed
        slot on the way (the passage actually RUNS, natural draws).
        """
        if self._time_machine is None:
            raise DebugError("the time machine is not wired")
        status = await self._time_machine.status(now=now)
        if status.get("season") is None:
            raise DebugError("no season is running")
        moment = status["now"]
        candidates = [
            (label, status[key])
            for label, key in (
                ("cycle (JdS)", "next_cycle_at"),
                ("âge", "next_age_at"),
                ("fin de saison", "season_end_at"),
            )
            if status.get(key) is not None and status[key] > moment
        ]
        if not candidates:
            raise DebugError("no upcoming event — the season is at its end")
        label, target = min(candidates, key=lambda pair: pair[1])
        seconds = int((target - moment).total_seconds())
        report = await self._time_machine.advance_seconds(seconds, now=moment, label=f"skippe → {label}")
        logger.warning(
            "kingdoms DEBUG: skippe to the next %s by admin (+%ds)",
            label,
            seconds,
        )
        return {"event": label, "target": target, "reports": report.get("reports")}

    # -- état ----------------------------------------------------------------

    async def status(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Report the debug state: bypass flag + running corbeau thefts."""
        moment = now or game_now()
        thefts = []
        for kingdom in await self._kingdoms.kingdoms():
            theft = kingdom.corbeau_theft
            if theft is not None and str(theft.get("state")) in (
                "pending",
                "awaiting_continue",
                "continuing",
            ):
                thefts.append(
                    {
                        "kingdom": kingdom.name,
                        "state": str(theft.get("state")),
                        "id": str(theft.get("id")),
                    }
                )
        return {"bypass": admin_bypass_enabled(), "thefts": thefts, "now": moment}


# ----------------------------------------------------------------------
# The /kingdoms-admin debug slash subgroup (admin-debug-v1)
# ----------------------------------------------------------------------

DEBUG_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "group_description": "The season test toolkit: targeted mutations and the admin bypass",
        "reset_name": "voliere-reset",
        "reset_description": "Land one volière bird now (or N minutes from now)",
        "bird_name": "bird",
        "bird_description": "corbeau, faucon, pigeon or all",
        "bird_arg": "bird",
        "bird_arg_description": "corbeau, faucon, pigeon or all",
        "minutes_arg": "minutes",
        "minutes_arg_description": "Ready-in minutes (default 0 = now)",
        "grant_name": "bird-grant",
        "grant_description": "Restore a flown or dead bird to its slot",
        "lock_name": "lock-clear",
        "lock_description": "Purge a marriage/Exploration/no-show lock of a lord",
        "lord_arg": "lord",
        "lord_arg_description": "The lord (id or display name)",
        "lock_arg": "lock",
        "lock_arg_description": "marriage, exploration, ffa or all",
        "attack_name": "attack-advance",
        "attack_description": "Force one running attack to its next phase",
        "attack_id_arg": "attack",
        "attack_id_arg_description": "The attack id",
        "stage_arg": "stage",
        "stage_arg_description": "reveal, pickban or expire",
        "bank_name": "bank",
        "bank_description": "Credit (or debit, negative) tech points on a kingdom",
        "kingdom_arg": "kingdom",
        "kingdom_arg_description": "The kingdom (id or name)",
        "delta_arg": "delta",
        "delta_arg_description": "Tech points to add (negative to debit)",
        "territory_name": "territory-grant",
        "territory_description": "Hand one territory to a kingdom",
        "map_key_arg": "territory",
        "map_key_arg_description": "The map key of the territory",
        "civ_name": "civ-grant",
        "civ_description": "Force a civilization into a kingdom's hand",
        "civ_arg": "civ",
        "civ_arg_description": "The civilization key",
        "chronicle_name": "chronicle-inject",
        "chronicle_description": "Append one fake chronicle entry on a lord",
        "kind_arg": "kind",
        "kind_arg_description": "The chronicle kind (e.g. marriage, ffa_result)",
        "params_arg": "params",
        "params_arg_description": "k=v pairs, comma-separated (e.g. civ=francs,tier=2)",
        "theft_name": "theft-advance",
        "theft_description": "Fast-forward the corbeau theft clock (execute/window)",
        "theft_stage_arg": "stage",
        "theft_stage_arg_description": "execute or window",
        "bypass_name": "bypass",
        "bypass_description": "Toggle the ADMIN BYPASS (gate checks of player buttons)",
        "bypass_state_arg": "state",
        "bypass_state_arg_description": "on or off",
        "status_name": "status",
        "status_description": "The debug state: bypass flag, running corbeau thefts",
        "denied": "You are not allowed to do that — this action is reserved for bot admins.",
        "done": "✅ {report}",
        "bypass_on": "🧪 ADMIN BYPASS **ON** — gate checks of the player buttons are bypassed.",
        "bypass_off": "🧪 ADMIN BYPASS **OFF** — every gate check applies again.",
        "status_line": "bypass: **{bypass}** — thefts: {thefts}",
    },
    "fr": {
        "group_description": "La boîte à outils de test de saison : mutations ciblées et bypass admin",
        "reset_name": "voliere-reset",
        "reset_description": "Poser un oiseau de la volière maintenant (ou dans N minutes)",
        "bird_name": "oiseau",
        "bird_description": "corbeau, faucon, pigeon ou all",
        "bird_arg": "oiseau",
        "bird_arg_description": "corbeau, faucon, pigeon ou all",
        "minutes_arg": "minutes",
        "minutes_arg_description": "Minutes d'attente (0 par défaut = maintenant)",
        "grant_name": "bird-grant",
        "grant_description": "Rendre son perchoir à un oiseau envolé ou mort",
        "lock_name": "lock-clear",
        "lock_description": "Purger un verrou mariage/Exploration/no-show d'un seigneur",
        "lord_arg": "seigneur",
        "lord_arg_description": "Le seigneur (id ou nom d'affichage)",
        "lock_arg": "verrou",
        "lock_arg_description": "marriage, exploration, ffa ou all",
        "attack_name": "attack-advance",
        "attack_description": "Forcer une attaque en cours vers sa phase suivante",
        "attack_id_arg": "attaque",
        "attack_id_arg_description": "L'identifiant de l'attaque",
        "stage_arg": "phase",
        "stage_arg_description": "reveal, pickban ou expire",
        "bank_name": "bank",
        "bank_description": "Créditer (ou débiter, négatif) des ⚙️ sur un royaume",
        "kingdom_arg": "royaume",
        "kingdom_arg_description": "Le royaume (id ou nom)",
        "delta_arg": "delta",
        "delta_arg_description": "Points à ajouter (négatif pour débiter)",
        "territory_name": "territory-grant",
        "territory_description": "Donner un territoire à un royaume",
        "map_key_arg": "territoire",
        "map_key_arg_description": "La clé de carte du territoire",
        "civ_name": "civ-grant",
        "civ_description": "Placer de force une civilisation dans la main d'un royaume",
        "civ_arg": "civ",
        "civ_arg_description": "La clé de la civilisation",
        "chronicle_name": "chronicle-inject",
        "chronicle_description": "Ajouter une entrée factice de chronique sur un seigneur",
        "kind_arg": "type",
        "kind_arg_description": "Le type de chronique (ex. marriage, ffa_result)",
        "params_arg": "params",
        "params_arg_description": "paires k=v séparées par des virgules (ex. civ=francs,tier=2)",
        "theft_name": "theft-advance",
        "theft_description": "Avancer l'horloge du vol corbeau (execute/window)",
        "theft_stage_arg": "phase",
        "theft_stage_arg_description": "execute ou window",
        "bypass_name": "bypass",
        "bypass_description": "Basculer le BYPASS ADMIN (gates des boutons joueurs)",
        "bypass_state_arg": "etat",
        "bypass_state_arg_description": "on ou off",
        "status_name": "status",
        "status_description": "L'état debug : flag bypass, vols corbeau en cours",
        "denied": "Vous n'êtes pas autorisé — cette action est réservée aux bot admins.",
        "done": "✅ {report}",
        "bypass_on": "🧪 BYPASS ADMIN **ACTIVÉ** — les gates des boutons joueurs sont contournées.",
        "bypass_off": "🧪 BYPASS ADMIN **DÉSACTIVÉ** — chaque gate s'applique à nouveau.",
        "status_line": "bypass : **{bypass}** — vols : {thefts}",
    },
}


def _debug_strings(locale: Any) -> dict[str, str]:
    key = "fr" if locale is not None and str(locale).startswith("fr") else "en"
    return DEBUG_STRINGS[key]


def register_debug_subgroup(group: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Attach the ``/kingdoms-admin debug`` slash subgroup (Drasah's tests)."""
    from discord import app_commands

    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]
    debug = app_commands.Group(
        name=localized("commands.kingdoms_admin_debug_name", "debug"),
        description=localized(
            "commands.kingdoms_admin_debug_description",
            strings["group_description"],
        ),
    )
    group.add_command(debug)
    _register_voliere_reset(debug, debug_service, guard)
    _register_bird_grant(debug, debug_service, guard)
    _register_lock_clear(debug, debug_service, guard)
    _register_attack_advance(debug, debug_service, guard)
    _register_bank(debug, debug_service, guard)
    _register_territory_grant(debug, debug_service, guard)
    _register_civ_grant(debug, debug_service, guard)
    _register_chronicle_inject(debug, debug_service, guard)
    _register_theft_advance(debug, debug_service, guard)
    _register_bypass(debug, debug_service, guard)
    _register_debug_status(debug, debug_service, guard)


async def _check(
    interaction: discord.Interaction,
    debug_service: Any,
    guard: Any,
) -> bool:
    """Defer, enforce the admin guard, answer when the toolkit is unwired."""
    await interaction.response.defer(ephemeral=True)
    if not await guard(interaction):
        return False
    if debug_service is None:
        await interaction.followup.send(_debug_strings(interaction.locale)["denied"], ephemeral=True)
        return False
    return True


async def _run(interaction: discord.Interaction, coro: Any) -> None:
    """Await one debug mutation and echo its report."""
    try:
        report = await coro
    except DebugError as error:
        await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
        return
    local = _debug_strings(interaction.locale)
    await interaction.followup.send(local["done"].format(report=_fmt_report(report)), ephemeral=True)


def _register_voliere_reset(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the volière cooldown reset subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_reset_name", strings["reset_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_reset_description",
            strings["reset_description"],
        ),
    )
    @app_commands.describe(
        kingdom=strings["kingdom_arg_description"],
        bird=strings["bird_arg_description"],
        minutes=strings["minutes_arg_description"],
    )
    @app_commands.choices(
        bird=[
            app_commands.Choice(name="corbeau", value="corbeau"),
            app_commands.Choice(name="faucon", value="faucon"),
            app_commands.Choice(name="pigeon", value="pigeon"),
            app_commands.Choice(name="all", value="all"),
        ]
    )
    async def voliere_reset(
        interaction: discord.Interaction,
        kingdom: str,
        bird: app_commands.Choice[str],
        minutes: int = 0,
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(
            interaction,
            debug_service.reset_voliere_cooldown(kingdom, str(bird.value), minutes),
        )


def _register_bird_grant(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the bird slot restoration subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_birdgrant_name", strings["grant_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_birdgrant_description",
            strings["grant_description"],
        ),
    )
    @app_commands.describe(
        kingdom=strings["kingdom_arg_description"],
        bird=strings["bird_arg_description"],
    )
    @app_commands.choices(
        bird=[
            app_commands.Choice(name="corbeau", value="corbeau"),
            app_commands.Choice(name="faucon", value="faucon"),
            app_commands.Choice(name="pigeon", value="pigeon"),
        ]
    )
    async def bird_grant(
        interaction: discord.Interaction,
        kingdom: str,
        bird: app_commands.Choice[str],
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.grant_bird(kingdom, str(bird.value)))


def _register_lock_clear(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the lord lock purge subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_lockclear_name", strings["lock_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_lockclear_description",
            strings["lock_description"],
        ),
    )
    @app_commands.describe(
        lord=strings["lord_arg_description"],
        lock=strings["lock_arg_description"],
    )
    @app_commands.choices(
        lock=[
            app_commands.Choice(name="marriage", value="marriage"),
            app_commands.Choice(name="exploration", value="exploration"),
            app_commands.Choice(name="ffa", value="ffa"),
            app_commands.Choice(name="all", value="all"),
        ]
    )
    async def lock_clear(
        interaction: discord.Interaction,
        lord: str,
        lock: app_commands.Choice[str],
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.clear_lock(lord, str(lock.value)))


def _register_attack_advance(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the attack phase-forcing subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_attackadvance_name", strings["attack_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_attackadvance_description",
            strings["attack_description"],
        ),
    )
    @app_commands.describe(
        attack_id=strings["attack_id_arg_description"],
        stage=strings["stage_arg_description"],
    )
    @app_commands.choices(
        stage=[
            app_commands.Choice(name="reveal", value="reveal"),
            app_commands.Choice(name="pickban", value="pickban"),
            app_commands.Choice(name="expire", value="expire"),
        ]
    )
    async def attack_advance(
        interaction: discord.Interaction,
        attack_id: str,
        stage: app_commands.Choice[str],
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.advance_attack(attack_id, str(stage.value)))


def _register_bank(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the tech-bank adjustment subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_bank_name", strings["bank_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_bank_description",
            strings["bank_description"],
        ),
    )
    @app_commands.describe(
        kingdom=strings["kingdom_arg_description"],
        delta=strings["delta_arg_description"],
    )
    async def bank(interaction: discord.Interaction, kingdom: str, delta: int) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.adjust_bank(kingdom, delta))


def _register_territory_grant(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the territory handover subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_territory_name", strings["territory_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_territory_description",
            strings["territory_description"],
        ),
    )
    @app_commands.describe(
        map_key=strings["map_key_arg_description"],
        kingdom=strings["kingdom_arg_description"],
    )
    async def territory_grant(interaction: discord.Interaction, map_key: str, kingdom: str) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.grant_territory(map_key, kingdom))


def _register_civ_grant(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the civilization grant subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_civ_name", strings["civ_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_civ_description",
            strings["civ_description"],
        ),
    )
    @app_commands.describe(
        kingdom=strings["kingdom_arg_description"],
        civ=strings["civ_arg_description"],
    )
    async def civ_grant(interaction: discord.Interaction, kingdom: str, civ: str) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.grant_civ(kingdom, civ))


def _register_chronicle_inject(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the chronicle injection subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_chronicle_name", strings["chronicle_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_chronicle_description",
            strings["chronicle_description"],
        ),
    )
    @app_commands.describe(
        lord=strings["lord_arg_description"],
        kind=strings["kind_arg_description"],
        params=strings["params_arg_description"],
    )
    async def chronicle_inject(
        interaction: discord.Interaction,
        lord: str,
        kind: str,
        params: str = "",
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        parsed: dict[str, str] = {}
        for chunk in params.split(","):
            if "=" in chunk:
                key, _, value = chunk.partition("=")
                parsed[key.strip()] = value.strip()
        await _run(interaction, debug_service.inject_chronicle(lord, kind, parsed))


def _register_theft_advance(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the corbeau theft clock subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_theft_name", strings["theft_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_theft_description",
            strings["theft_description"],
        ),
    )
    @app_commands.describe(
        kingdom=strings["kingdom_arg_description"],
        stage=strings["theft_stage_arg_description"],
    )
    @app_commands.choices(
        stage=[
            app_commands.Choice(name="execute", value="execute"),
            app_commands.Choice(name="window", value="window"),
        ]
    )
    async def theft_advance(
        interaction: discord.Interaction,
        kingdom: str,
        stage: app_commands.Choice[str],
    ) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        await _run(interaction, debug_service.advance_theft(kingdom, str(stage.value)))


def _register_bypass(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the ADMIN BYPASS toggle subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_bypass_name", strings["bypass_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_bypass_description",
            strings["bypass_description"],
        ),
    )
    @app_commands.describe(state=strings["bypass_state_arg_description"])
    @app_commands.choices(
        state=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off"),
        ]
    )
    async def bypass(interaction: discord.Interaction, state: app_commands.Choice[str]) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        enabled = str(state.value) == "on"
        set_admin_bypass(enabled, source="slash")
        local = _debug_strings(interaction.locale)
        await interaction.followup.send(local["bypass_on"] if enabled else local["bypass_off"], ephemeral=True)


def _register_debug_status(debug: app_commands.Group, debug_service: Any, guard: Any) -> None:
    """Register the debug status subcommand."""
    from kingdoms.discord.commands_i18n import localized

    strings = DEBUG_STRINGS["en"]

    @debug.command(
        name=localized("commands.kingdoms_admin_debug_status_name", strings["status_name"]),
        description=localized(
            "commands.kingdoms_admin_debug_status_description",
            strings["status_description"],
        ),
    )
    async def debug_status(interaction: discord.Interaction) -> None:
        if not await _check(interaction, debug_service, guard):
            return
        local = _debug_strings(interaction.locale)
        report = await debug_service.status()
        thefts = ", ".join(f"{entry['kingdom']} ({entry['state']})" for entry in report.get("thefts", [])) or "—"
        await interaction.followup.send(
            local["status_line"].format(
                bypass="ON" if report.get("bypass") else "OFF",
                thefts=thefts,
            ),
            ephemeral=True,
        )


def _fmt_report(report: Any) -> str:
    """Render one debug-mutation report dict as a compact line."""
    if not isinstance(report, dict):
        return str(report)
    parts = []
    for key, value in report.items():
        if key == "now":
            continue
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        parts.append(f"{key}={value}")
    return " ".join(parts)
