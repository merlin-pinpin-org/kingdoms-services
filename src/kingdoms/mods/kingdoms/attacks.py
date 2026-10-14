"""Kingdoms mod attacks & defenses service (kingdoms-services#158, T4).

Reference §11-§13 and decisions D1, D6-D8, D10-D12, D17-D18, D20-D21
(not names), D27-D28, D39, D41-D44: the attack state machine (kingdom
vs kingdom and the Gaïa free-for-all), the configurable delays, the
weekly attack/defense budgets recharged at each cycle switch, and the
six combat technologies (Embuscade, Traquenard, Jeu d'armes, Patrouille,
Contre-espionnage, Sabotage).

Results come from the game contract through the admin (D20): the
service only owns the state machine and applies the idempotent
territory transfer on capture.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from kingdoms.mods.kingdoms.events import active_lords_day_window
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    AttackKind,
    AttackModel,
    AttackState,
    TechnologyState,
)
from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import KingdomsSeasonConfig
    from kingdoms.mods.kingdoms.models import (
        KingdomModel,
        LordModel,
        SeasonState,
        TerritoryModel,
    )
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.attacks")

COMBAT_TECHNOLOGIES = (
    "embuscade",
    "traquenard",
    "contre_espionnage",
    "sabotage",
    "jeu_d_armes",
)


class AttackError(KingdomsModError):
    """Base of the attack-domain errors."""

    code = "KINGDOMS_ATTACK_ERROR"
    message_key = "kingdoms.errors.attack_unexpected"


class NotEnrolledError(AttackError):
    """Raised when the player has no lord document in the season."""

    code = "KINGDOMS_NOT_ENROLLED"
    message_key = "kingdoms.errors.not_enrolled"


class NoBudgetError(AttackError):
    """Raised when the weekly attack/defense budget is spent (§11, D1)."""

    code = "KINGDOMS_NO_BUDGET"
    message_key = "kingdoms.errors.no_budget"


class TerritoryNotAttackableError(AttackError):
    """Raised when the target is not an adversary territory (own/Gaïa rules)."""

    code = "KINGDOMS_TERRITORY_NOT_ATTACKABLE"
    message_key = "kingdoms.errors.territory_not_attackable"


class TerritoryBusyError(AttackError):
    """Raised when the territory already has an ongoing attack (D42)."""

    code = "KINGDOMS_TERRITORY_BUSY"
    message_key = "kingdoms.errors.territory_busy"


class AttackNotFoundError(AttackError):
    """Raised when no attack matches in the current data set."""

    code = "KINGDOMS_ATTACK_NOT_FOUND"
    message_key = "kingdoms.errors.attack_not_found"


class AttackStateError(AttackError):
    """Raised on an illegal transition of the attack state machine."""

    code = "KINGDOMS_ATTACK_STATE"
    message_key = "kingdoms.errors.attack_state"


class GaiaAttackFullError(AttackError):
    """Raised when a Gaïa free-for-all accepts no more participant (§13.2)."""

    code = "KINGDOMS_GAIA_ATTACK_FULL"
    message_key = "kingdoms.errors.gaia_attack_full"


class TechnologyError(AttackError):
    """Base of the technology purchase errors (D9/D36)."""

    code = "KINGDOMS_TECHNOLOGY_ERROR"
    message_key = "kingdoms.errors.technology_unexpected"


class UnknownTechnologyError(TechnologyError):
    """Raised when the technology name is not in the catalog."""

    code = "KINGDOMS_UNKNOWN_TECHNOLOGY"
    message_key = "kingdoms.errors.unknown_technology"


class InsufficientTechPointsError(TechnologyError):
    """Raised when the kingdom cannot afford the purchase (D9)."""

    code = "KINGDOMS_INSUFFICIENT_TECH_POINTS"
    message_key = "kingdoms.errors.insufficient_tech_points"


class TechnologyLimitReachedError(TechnologyError):
    """Raised when the per-season purchase limit is reached (D36)."""

    code = "KINGDOMS_TECHNOLOGY_LIMIT"
    message_key = "kingdoms.errors.technology_limit"


class SabotageError(TechnologyError):
    """Raised on an illegal sabotage (D12/D28)."""

    code = "KINGDOMS_SABOTAGE_ERROR"
    message_key = "kingdoms.errors.sabotage_error"


class LordLockedError(AttackError):
    """Raised when a fresh marriage locks a lord out of combat (D59/D60)."""

    code = "KINGDOMS_LORD_LOCKED"
    message_key = "kingdoms.errors.lord_locked"


class LordsDayAttackError(AttackError):
    """Raised when an attack would be played inside the Lord's Day window.

    Drasah's window rule (2026-10-11): between the weekly switch
    (Sunday 23:30) and Monday 10:00 an attack cannot be PLAYED — but a
    DECLARATION is accepted when the execution lands after the window
    closes (the attack's expiry sits past Monday 10:00).
    """

    code = "KINGDOMS_LORDS_DAY"
    message_key = "kingdoms.errors.lords_day"


class SeasonClosedError(AttackError):
    """Raised when an attack action targets a finished season.

    Drasah's endcycle rule (2026-10-11): the verdict lands on the
    endcycle's Sunday at 23:59 — every attack is cancelled then and
    nothing combat-related may happen until the next season launch.
    """

    code = "KINGDOMS_SEASON_CLOSED"
    message_key = "kingdoms.errors.season_closed"


class PatrolProtectedError(AttackError):
    """Raised when the target kingdom's patrol covers the moment (D68)."""

    code = "KINGDOMS_PATROL_PROTECTED"
    message_key = "kingdoms.errors.patrol_protected"


class DefenseCutoffError(AttackError):
    """Raised when the defense closes at T-15 (phases d'attaque, D77)."""

    code = "KINGDOMS_DEFENSE_CUTOFF"
    message_key = "kingdoms.errors.defense_cutoff"


class PickBanError(AttackError):
    """Base of the pick & ban errors (pré-combat, D77)."""

    code = "KINGDOMS_PICKBAN_ERROR"
    message_key = "kingdoms.errors.pickban"


class PickBanStateError(PickBanError):
    """Raised when the pick & ban happens outside its window (D77)."""

    code = "KINGDOMS_PICKBAN_STATE"
    message_key = "kingdoms.errors.pickban_state"


class EmbuscadeError(AttackError):
    """Raised when the Embuscade cannot engage (D77)."""

    code = "KINGDOMS_EMBUSCADE"
    message_key = "kingdoms.errors.embuscade"


PATROUILLE_SLOT_KEYS = ("patrouille_slot_1", "patrouille_slot_2")
PATROUILLE_HOURS = 2
PATROUILLE_REINFORCED_HOURS = 3
PATROUILLE_TZ = "Europe/Paris"
AI_LEVEL_CAP = 5
TRAQUENARD_LOCK_HOURS = 2
TRAQUENARD_WINDOW_HOURS = 6


def patrouille_slots(state: TechnologyState) -> tuple[int, ...]:
    """Return the kingdom's bought patrol slots (start hours, D68)."""
    return tuple(
        state.purchases[key]
        for key in PATROUILLE_SLOT_KEYS
        if key in state.purchases
    )


def patrouille_reinforced(state: TechnologyState, base_level: int) -> bool:
    """Whether the kingdom's patrols are Renforcées (marche-v2 art. 5).

    The transformation is automatic and free: when the defence bot's AI
    reaches the ceiling (5, Extrême — the epoch base level plus one per
    permanent Jeu d'armes purchase), every patrol slot of the kingdom
    widens from 2h to 3h.
    """
    return (
        min(AI_LEVEL_CAP, base_level + state.purchases.get("jeu_d_armes", 0))
        >= AI_LEVEL_CAP
    )


def patrouille_active_at(
    state: TechnologyState, now: datetime, *, base_level: int | None = None
) -> bool:
    """Whether a patrol slot covers ``now`` (daily 2h slot, D68).

    The slot hours are expressed in the players' timezone (Europe/Paris,
    the community's convention); an environment without tzdata falls
    back to UTC so the rule keeps working everywhere. With a known
    ``base_level`` the Renforcée widening (3h, marche-v2 art. 5)
    applies as soon as the defence AI sits at the ceiling.
    """
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(PATROUILLE_TZ))
    except Exception:
        local = now
    hours = (
        PATROUILLE_REINFORCED_HOURS
        if base_level is not None and patrouille_reinforced(state, base_level)
        else PATROUILLE_HOURS
    )
    hour = local.hour + local.minute / 60
    return any(slot <= hour < slot + hours for slot in patrouille_slots(state))


def _now() -> datetime:
    """Return the current UTC time (seam for deterministic tests)."""
    return datetime.now(tz=UTC)


def _refuse_lords_day_execution(
    window: tuple[datetime, datetime] | None, timestamp: datetime, delay_hours: int
) -> None:
    """Raise when an attack declared at ``timestamp`` would play inside the window.

    Drasah's declare-vs-play rule: declaring during the Lord's Day
    window stays possible, but only when the execution (the expiry
    deadline, ``delay_hours`` after the declaration) lands after the
    window closes (Monday 10:00) — a game that would be played inside
    the window is refused.
    """
    if window is None:
        return
    expires = timestamp + timedelta(hours=delay_hours)
    if expires <= window[1]:
        raise LordsDayAttackError(
            "the attack would be played inside the Lord's Day window "
            "(declare only if the execution lands after it closes)"
        )


def _apply_sabotages(pickban: dict[str, Any]) -> dict[str, Any]:
    """Apply both sides' sabotages to the targeted picks (D77).

    A sabotaged civilization leaves the adversary's ranked picks — the
    played civ is the best-ranked NON-sabotaged pick of each side. The
    sabotages themselves stay recorded for the game setup.
    """
    result = dict(pickban)
    attacker_sabotages = list(result.get("attacker_sabotages") or [])
    defender_sabotages = list(result.get("defender_sabotages") or [])
    result["attacker_picks"] = [
        civ for civ in (result.get("attacker_picks") or []) if civ not in defender_sabotages
    ]
    result["defender_picks"] = [
        civ for civ in (result.get("defender_picks") or []) if civ not in attacker_sabotages
    ]
    return result

CORBEAU_TRACK_DELAY_HOURS = 24
"""Volière Corbeau (voliere-v1, Drasah 14/10): the delay of every attack
a « suivi » lord declares — 24h instead of the configured delay, for
the whole 24h tracking window."""


def _faucon_released(kingdom: Any, timestamp: datetime) -> bool:
    """Whether the kingdom runs an active Lâcher de faucon (voliere-v1)."""
    return int(getattr(kingdom, "faucon_release_until", 0) or 0) > int(timestamp.timestamp())



class AttackService:
    """The attack/defense state machine and combat economy (§11-§13)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
    ) -> None:
        """Store the seams: persistence, config, enrollment, territories."""
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service

    # ------------------------------------------------------------------
    # Attack declaration (§11-§12, D27/D41/D42)
    # ------------------------------------------------------------------
    async def attacks(self) -> list[AttackModel]:
        """Return every attack of the current data set."""
        return [AttackModel.from_mongo(doc) for doc in await self._store.find_attacks()]

    @property
    def gaia_max_participants(self) -> int:
        """The configured Gaïa free-for-all participant ceiling (§13)."""
        return int(self._config.attacks.gaia_max_participants)

    async def declare_attack(
        self,
        player_id: str,
        map_key: str,
        lobby_url: str,
        *,
        cover_lord_id: str | None = None,
        now: datetime | None = None,
    ) -> AttackModel:
        """Declare a kingdom-vs-kingdom attack on an adversary territory.

        Any lord declares (D27), the target must be free of any ongoing
        attack (D42), the attacker must have an attack budget left, and
        the declared territory owner must be another player kingdom.
        The optional cover (Contre-espionnage, D77) displays another
        lord instead of the real attacker until the T-15 reveal — one
        bought cover is consumed on use. The Traquenard v2 (Drasah
        2026-10-14) is INDEPENDENT of the declaration: the kingdom
        activates it from its own salon (see ``activate_traquenard``)
        and every attack declared against the target inside the 6h
        window faces the Facile AI — the declaration merely RECORDS the
        flag for the reveal.
        """
        season = await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        timestamp = now or _now()
        territory = await self._validate_attack_target(lord, map_key, timestamp)
        cover = await self._engage_cover(lord, cover_lord_id) if cover_lord_id else None
        if lord.attack_used >= self._config.attacks.attacks_per_week:
            raise NoBudgetError("the weekly attack budget is spent")
        if not lobby_url.strip():
            raise AttackError("the attacker must provide the game lobby link")
        # Volière Corbeau (voliere-v1): a « suivi » lord declares with
        # the 24h delay for the whole 24h tracking window.
        delay_hours = self._config.attacks.player_attack_delay_hours
        if await self._lord_tracked_by_corbeau(lord.id, timestamp):
            delay_hours = CORBEAU_TRACK_DELAY_HOURS
        # Drasah's Lord's Day window: declaring inside the window is
        # fine ONLY when the execution (the attack's expiry deadline)
        # lands after the window closes (Monday 10:00) — a game that
        # would be played inside the window is refused.
        _refuse_lords_day_execution(
            await self._active_lords_day_window(timestamp),
            timestamp,
            delay_hours,
        )
        lord.attack_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        target_kingdom = await self._kingdom_by_id(territory.owner_kingdom_id)
        traquenard = int(getattr(target_kingdom, "traquenard_until", 0) or 0) > int(
            timestamp.timestamp()
        )
        attack = AttackModel(
            _id=f"{season.id}-a-{territory.id}-{lord.id}",
            season_id=season.id,
            kind=AttackKind.PLAYER,
            territory_id=territory.id,
            map_key=territory.map_key,
            defender_kingdom_id=territory.owner_kingdom_id,
            attacker_lord_id=lord.id,
            attacker_kingdom_id=lord.kingdom_id,
            lobby_url=lobby_url.strip(),
            declared_at=timestamp,
            expires_at=timestamp + timedelta(hours=delay_hours),
            cover_lord_id=cover,
            traquenard=traquenard,
        )
        await self._store.upsert_attack(attack.to_mongo())
        logger.info(
            "kingdoms: %s declared an attack on %s (expires %s%s%s)",
            lord.id,
            territory.map_key,
            attack.expires_at.isoformat(),
            " under cover" if cover else "",
            " through an active Traquenard" if traquenard else "",
        )
        return attack

    async def _lord_tracked_by_corbeau(self, lord_id: str, timestamp: datetime) -> bool:
        """Whether any kingdom currently « suit » this lord (voliere-v1)."""
        for kingdom in await self._kingdoms.kingdoms():
            for entry in kingdom.corbeau_tracking or []:
                if (
                    str(entry.get("lord_id")) == lord_id
                    and int(entry.get("until", 0) or 0) > int(timestamp.timestamp())
                ):
                    return True
        return False

    async def _void_faucon_sabotages(
        self, attack: AttackModel, pickban: dict[str, Any], timestamp: datetime
    ) -> dict[str, Any]:
        """Volière Lâcher de faucon: a released side suffers no sabotage."""
        kingdoms = {k.id: k for k in await self._kingdoms.kingdoms()}
        out = dict(pickban)
        attacker = kingdoms.get(attack.attacker_kingdom_id)
        defender = kingdoms.get(attack.defender_kingdom_id)
        if attacker is not None and _faucon_released(attacker, timestamp):
            out["defender_sabotages"] = []
        if defender is not None and _faucon_released(defender, timestamp):
            out["attacker_sabotages"] = []
        return out

    async def declare_embuscade(
        self,
        player_id: str,
        map_key: str,
        lobby_url: str,
        *,
        now: datetime | None = None,
    ) -> AttackModel:
        """Declare the exceptional Embuscade (D77): T-1h, no weekly budget.

        The attack meets at T = declaration + 1h sharp — the defense
        window shrinks to the 45 minutes before the T-15 cutoff. One
        bought Embuscade is consumed on use; the weekly budget stays
        intact. No cover: the Embuscade strikes in the open.
        """
        season = await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        timestamp = now or _now()
        territory = await self._validate_attack_target(lord, map_key, timestamp)
        # Volière Lâcher de faucon (voliere-v1): the Embuscade is an
        # adverse technology effect — a released defender is immune.
        target_kingdom = await self._kingdom_by_id(territory.owner_kingdom_id)
        if _faucon_released(target_kingdom, timestamp):
            raise EmbuscadeError("the defender released the faucon — immune to technologies")
        if not lobby_url.strip():
            raise AttackError("the attacker must provide the game lobby link")
        _refuse_lords_day_execution(
            await self._active_lords_day_window(timestamp),
            timestamp,
            self._config.attacks.embuscade_delay_hours,
        )
        state = await self.technology_state(lord.kingdom_id)
        bought = state.purchases.get("embuscade", 0)
        if bought <= 0:
            raise EmbuscadeError("the kingdom bought no Embuscade")
        state.purchases["embuscade"] = bought - 1
        await self._store.upsert_technology(state.to_mongo())
        attack = AttackModel(
            _id=f"{season.id}-a-{territory.id}-{lord.id}",
            season_id=season.id,
            kind=AttackKind.PLAYER,
            territory_id=territory.id,
            map_key=territory.map_key,
            defender_kingdom_id=territory.owner_kingdom_id,
            attacker_lord_id=lord.id,
            attacker_kingdom_id=lord.kingdom_id,
            lobby_url=lobby_url.strip(),
            declared_at=timestamp,
            expires_at=timestamp + timedelta(hours=self._config.attacks.embuscade_delay_hours),
            embuscade=True,
        )
        await self._store.upsert_attack(attack.to_mongo())
        logger.info(
            "kingdoms: %s declared an EMBUSCADE on %s (expires %s)",
            lord.id,
            territory.map_key,
            attack.expires_at.isoformat(),
        )
        return attack

    async def _validate_attack_target(
        self, lord: LordModel, map_key: str, timestamp: datetime
    ) -> TerritoryModel:
        """Run the shared declaration checks on one target territory."""
        territory = await self._territory_by_map(map_key)
        if territory.owner_kingdom_id == lord.kingdom_id:
            raise TerritoryNotAttackableError("a kingdom cannot attack its own territory")
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if owner.is_gaia:
            raise TerritoryNotAttackableError("Gaïa territories are attacked through the free-for-all")
        if territory.is_protected_at(timestamp):
            raise TerritoryNotAttackableError("the territory is protected (Garde Royale/Corruption)")
        # D68: the target kingdom's daily patrol covers all its
        # territories during the bought slot (3h once Renforcée, art. 5).
        if patrouille_active_at(
            await self.technology_state(territory.owner_kingdom_id),
            timestamp,
            base_level=await self.base_ai_level(),
        ):
            raise PatrolProtectedError("the target kingdom's patrol protects its territories (D68)")
        # D59/D60: a fresh marriage locks the lord out of combat.
        if lord.marriage_locked_until is not None and lord.marriage_locked_until > int(
            timestamp.timestamp()
        ):
            raise LordLockedError("the lord is locked by a fresh marriage (D59/D60)")
        # marche-v2 art. 6: a lord sent exploring is locked out of
        # combat for the whole 48h exploration (fixed lock).
        if lord.exploration_locked_until is not None and lord.exploration_locked_until > int(
            timestamp.timestamp()
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        if await self._territory_busy(territory.id):
            raise TerritoryBusyError("the territory already has an ongoing attack")
        return territory

    async def _engage_cover(self, lord: LordModel, cover_lord_id: str) -> str:
        """Validate and consume one cover (Contre-espionnage, D77)."""
        if cover_lord_id == lord.id:
            raise AttackError("the cover lord cannot be the attacker himself")
        cover = await self._require_lord(cover_lord_id)
        if cover.left or cover.kingdom_id is None:
            raise AttackError("the cover lord is no active lord of the season")
        state = await self.technology_state(lord.kingdom_id or "")
        bought = state.purchases.get("contre_espionnage", 0)
        if bought <= 0:
            raise AttackError("the kingdom bought no Contre-espionnage")
        state.purchases["contre_espionnage"] = bought - 1
        await self._store.upsert_technology(state.to_mongo())
        return cover.id

    async def respond_defense(self, attack_id: str, player_id: str) -> AttackModel:
        """Answer an attack: any lord of the defending kingdom (D7).

        The defense closes at T-15 (D77): the cutoff moment is
        ``expires_at - defense_cutoff_minutes`` — after that the
        defender can no longer react and the kingdom's bot AI takes
        the defense instead.
        """
        await self._require_running_season()
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("a Gaïa free-for-all has no defending side")
        if attack.state is not AttackState.DECLARED:
            raise AttackStateError("the attack no longer waits for a defender")
        timestamp = _now()
        cutoff = attack.expires_at - timedelta(minutes=self._config.attacks.defense_cutoff_minutes)
        if timestamp >= cutoff:
            raise DefenseCutoffError(
                "the defense is closed (T-15) — the meeting is set and the bot AI takes over"
            )
        lord = await self._require_lord(player_id)
        if lord.kingdom_id != attack.defender_kingdom_id or lord.left:
            raise AttackStateError("only a lord of the defending kingdom can defend")
        # D59/D60: a fresh marriage locks the lord out of combat.
        if lord.marriage_locked_until is not None and lord.marriage_locked_until > int(
            _now().timestamp()
        ):
            raise LordLockedError("the lord is locked by a fresh marriage (D59/D60)")
        # marche-v2 art. 6: the exploring lord cannot defend either.
        if lord.exploration_locked_until is not None and lord.exploration_locked_until > int(
            _now().timestamp()
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        if lord.defense_used >= self._config.attacks.defenses_per_week:
            raise NoBudgetError("the weekly defense budget is spent")
        lord.defense_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        attack.state = AttackState.DEFENDED
        attack.defender_lord_id = lord.id
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: %s defends the attack on %s", lord.id, attack.map_key)
        return attack

    async def reveal_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Run the T-15 reveal (D77): close the defense, unmask the cover.

        Idempotent through ``revealed`` — the scheduler tick can replay
        it after every restart. When nobody answered, the defending
        kingdom's bot AI takes the defense (the D77 vs_ai rule; the
        legacy auto_victory capture only applies at expiry for attacks
        the reveal never reached).
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        if await self._active_lords_day_window(timestamp) is not None:
            return []
        revealed: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.revealed or attack.is_over:
                continue
            cutoff = attack.expires_at - timedelta(
                minutes=self._config.attacks.defense_cutoff_minutes
            )
            if timestamp < cutoff:
                continue
            attack.revealed = True
            if attack.state is AttackState.DECLARED and attack.defender_lord_id is None:
                attack.ai_defender = True
                attack.state = AttackState.DEFENDED
                # marche-v2 art. 2/5: the level the attacker faces — a
                # Traquenard forces Facile (1), otherwise the defence
                # bot's own level (epoch base + Jeu d'armes, ceiling 5).
                attack.effects["ai_level"] = (
                    1
                    if attack.traquenard
                    else await self.defense_ai_level(attack.defender_kingdom_id)
                )
                logger.info(
                    "kingdoms: no defender on %s — the bot AI of %s takes the defense (level %s)",
                    attack.map_key,
                    attack.defender_kingdom_id,
                    attack.effects["ai_level"],
                )
            await self._store.upsert_attack(attack.to_mongo())
            revealed.append(attack)
        return revealed

    async def pick_ban_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Mark the attacks whose T-5 pick & ban DMs must leave (D77).

        Idempotent through ``pickban["sent"]``. The AI side
        auto-validates at the same moment (no human to wait for).
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        due: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.DEFENDED:
                continue
            if attack.pickban.get("sent") or attack.is_over:
                continue
            lead = attack.expires_at - timedelta(
                minutes=self._config.attacks.pick_ban_lead_minutes
            )
            if timestamp < lead or timestamp >= attack.expires_at:
                continue
            pickban = dict(attack.pickban)
            pickban["sent"] = True
            if attack.ai_defender:
                pickban["defender_validated"] = True  # the AI never stalls
            attack.pickban = pickban
            await self._store.upsert_attack(attack.to_mongo())
            due.append(attack)
        return due

    async def set_picks(
        self,
        attack_id: str,
        player_id: str,
        picks: list[str],
        *,
        sabotages: list[str] | None = None,
        now: datetime | None = None,
    ) -> AttackModel:
        """Submit one side's ranked civilizations and sabotages (D77).

        Each side ranks up to 3 civilizations from ITS OWN hand (blind
        pick — the adversary's hand is public through Alliances, the
        espionage stays legal) and engages up to 2 sabotages against
        civilizations of the ADVERSARY hand. Only the declaring lord
        picks for the attack side; only the lord who took the defense
        picks for the defense side.
        """
        attack = await self._require_pickban_attack(attack_id, now or _now())
        side = await self._pick_ban_side(attack, player_id)
        hand = await self._side_hand(
            attack.attacker_kingdom_id if side == "attacker" else attack.defender_kingdom_id
        )
        required = min(3, len(hand))
        if len(picks) != required or len(set(picks)) != required:
            raise PickBanStateError(f"rank exactly {required} distinct civilization(s)")
        if any(pick not in hand for pick in picks):
            raise PickBanStateError("every pick must come from your kingdom's hand")
        sabotages = sabotages or []
        if len(sabotages) > 2:
            raise PickBanStateError("at most two sabotages per side")
        adversary_hand = await self._side_hand(
            attack.defender_kingdom_id if side == "attacker" else attack.attacker_kingdom_id
        )
        if any(civ not in adversary_hand for civ in sabotages):
            raise PickBanStateError("every sabotage targets the adversary's hand")
        pickban = dict(attack.pickban)
        pickban[f"{side}_picks"] = list(picks)
        pickban[f"{side}_sabotages"] = list(sabotages)
        attack.pickban = pickban
        await self._store.upsert_attack(attack.to_mongo())
        logger.info(
            "kingdoms: pick & ban submitted by %s on the attack on %s", player_id, attack.map_key
        )
        return attack

    async def validate_pickban(
        self, attack_id: str, player_id: str, *, now: datetime | None = None
    ) -> AttackModel:
        """Validate one side's pick & ban (D77).

        When both sides validated, the sabotages apply (a sabotaged
        civilization leaves the targeted picks) and the civilizations
        are revealed — the played civ is the best-ranked non-sabotaged
        pick of each side.
        """
        attack = await self._require_pickban_attack(attack_id, now or _now())
        side = await self._pick_ban_side(attack, player_id)
        pickban = dict(attack.pickban)
        pickban[f"{side}_validated"] = True
        if pickban.get("attacker_validated") and pickban.get("defender_validated"):
            pickban = _apply_sabotages(
                await self._void_faucon_sabotages(attack, pickban, now or _now())
            )
            pickban["resolved_pickban"] = True
        attack.pickban = pickban
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: pick & ban validated by %s on %s", player_id, attack.map_key)
        return attack

    async def _require_pickban_attack(self, attack_id: str, timestamp: datetime) -> AttackModel:
        """Fetch the attack and enforce the pré-combat window (D77)."""
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise PickBanStateError("the free-for-all has no pick & ban")
        if attack.state is not AttackState.DEFENDED:
            raise PickBanStateError("the pick & ban is not open on this attack")
        if attack.pickban.get("resolved_pickban"):
            raise PickBanStateError("the pick & ban is already resolved")
        if not attack.pickban.get("sent"):
            raise PickBanStateError("the pick & ban opens at T-5")
        if timestamp >= attack.expires_at:
            raise PickBanStateError("the meeting time is reached — the pick & ban is closed")
        return attack

    async def _pick_ban_side(self, attack: AttackModel, player_id: str) -> str:
        """Resolve which side the picking player speaks for (D77)."""
        lord = await self._require_lord(player_id)
        if lord.kingdom_id == attack.attacker_kingdom_id and lord.id == attack.attacker_lord_id:
            return "attacker"
        if (
            lord.kingdom_id == attack.defender_kingdom_id
            and lord.id == attack.defender_lord_id
        ):
            return "defender"
        raise PickBanStateError("only the two players of the meeting pick & ban")

    async def _side_hand(self, kingdom_id: str) -> list[str]:
        """Return the kingdom's civilizations (its Alliances hand, D77)."""
        kingdom = await self._kingdom_by_id(kingdom_id)
        return list(kingdom.civilizations or [])

    async def expire_stale(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Apply the meeting-time outcome to every expired attack (D8/D77).

        At T (``expires_at``): a defense never locked moves to the
        kingdom's bot AI (vs_ai default) — legacy ``auto_victory``
        captures without a game when configured. A locked defense
        whose pick & ban never resolved is a FORFAIT: the side that
        missed its validation loses (both missed → the attacker, the
        present, wins). A resolved pick & ban keeps waiting for the
        game result (D20) — the RDV itself.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        # Lord's Day window: the automatic outcomes are executions too —
        # they are deferred until the window closes (the next call picks
        # the stale attacks up).
        if await self._active_lords_day_window(timestamp) is not None:
            return []
        expired: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.is_over or attack.expires_at > timestamp:
                continue
            handled = await self._expire_one(attack, timestamp)
            if handled is None:
                continue
            expired.append(attack)
            logger.info("kingdoms: attack on %s expired (%s)", attack.map_key, attack.state)
        return expired

    async def _expire_one(self, attack: AttackModel, timestamp: datetime) -> AttackModel | None:
        """Apply the meeting-time outcome to ONE expired attack (D77).

        Returns the attack when its state changed (it joins the
        expired list), ``None`` when the outcome keeps waiting for
        the game result (D20) or nothing applies.
        """
        if attack.state is AttackState.DECLARED:
            if self._config.attacks.no_defense_outcome == "auto_victory":
                attack.state = AttackState.RESOLVED
                attack.winner_kingdom_id = attack.attacker_kingdom_id
                attack.resolved_at = timestamp
                await self._territories.transfer(attack.territory_id, attack.attacker_kingdom_id)
            else:
                # Catch-up of a reveal the tick never ran (restart):
                # the bot AI takes the defense at the meeting time.
                attack.revealed = True
                attack.ai_defender = True
                attack.state = AttackState.DEFENDED
                attack.pickban = {
                    **attack.pickban,
                    "sent": True,
                    "defender_validated": True,
                }
            await self._store.upsert_attack(attack.to_mongo())
            return attack
        if attack.state is not AttackState.DEFENDED or attack.pickban.get("resolved_pickban"):
            return None
        return await self._expire_defended(attack, timestamp)

    async def _expire_defended(
        self, attack: AttackModel, timestamp: datetime
    ) -> AttackModel | None:
        """Resolve the un-resolved pick & ban of an expired defense (D77)."""
        pickban = dict(attack.pickban)
        attacker_ok = bool(pickban.get("attacker_validated"))
        defender_ok = bool(pickban.get("defender_validated")) or attack.ai_defender
        if attacker_ok and defender_ok:
            pickban = _apply_sabotages(
                await self._void_faucon_sabotages(attack, pickban, timestamp)
            )
            pickban["resolved_pickban"] = True
            attack.pickban = pickban
            await self._store.upsert_attack(attack.to_mongo())
            return None  # validated at the wire — the game decides (D20)
        if attacker_ok:
            winner, forfeit = attack.attacker_kingdom_id, "defender"
        elif defender_ok:
            winner, forfeit = attack.defender_kingdom_id, "attacker"
        else:
            winner, forfeit = attack.attacker_kingdom_id, "both"  # the present wins
        pickban["forfeit_side"] = forfeit
        attack.pickban = pickban
        attack.state = AttackState.RESOLVED
        attack.winner_kingdom_id = winner
        attack.resolved_at = timestamp
        if winner == attack.attacker_kingdom_id:
            await self._territories.transfer(attack.territory_id, winner)
        await self._store.upsert_attack(attack.to_mongo())
        return attack

    async def cancel_ongoing(self) -> list[AttackModel]:
        """Cancel every ongoing attack before the season verdict.

        Drasah's endcycle rule (2026-10-11): at the endcycle's Sunday
        23:59 the ongoing attacks (declared or defended) are CANCELLED
        so the verdict counts a clean board — they move to ``expired``
        and never resolve afterwards.
        """
        cancelled: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.state not in (AttackState.DECLARED, AttackState.DEFENDED):
                continue
            attack.state = AttackState.EXPIRED
            await self._store.upsert_attack(attack.to_mongo())
            cancelled.append(attack)
            logger.info("kingdoms: attack on %s cancelled (season end)", attack.map_key)
        return cancelled

    async def resolve(self, attack_id: str, winner_kingdom_id: str) -> AttackModel:
        """Apply the game result (D20) and transfer on capture, idempotently.

        The winner is either the attacker (capture) or the defender
        (conservation); Gaïa can also keep its territory in a
        free-for-all nobody won.
        """
        await self._require_running_season()
        if await self._active_lords_day_window(_now()) is not None:
            raise LordsDayAttackError(
                "the attack cannot be played inside the Lord's Day window "
                "(resolve it after the window closes)"
            )
        attack = await self._find_attack(attack_id)
        if attack.state is AttackState.RESOLVED:
            if attack.winner_kingdom_id == winner_kingdom_id:
                return attack  # idempotent replay of the same result
            raise AttackStateError("the attack is already resolved with another winner")
        # A Gaïa free-for-all has no defending step (D44): it resolves
        # from DECLARED once the game result comes in.
        allowed_states: tuple[AttackState, ...] = (AttackState.DEFENDED, AttackState.EXPIRED)
        if attack.kind is AttackKind.GAIA:
            allowed_states = (AttackState.DECLARED, AttackState.DEFENDED, AttackState.EXPIRED)
        if attack.state not in allowed_states:
            raise AttackStateError("the attack awaits a defender or its expiry")
        kingdoms = await self._kingdoms.kingdoms()
        known = {kingdom.id for kingdom in kingdoms}
        if winner_kingdom_id not in known:
            raise KingdomNotFoundError("the winner kingdom does not exist in the season")
        allowed = {attack.attacker_kingdom_id, attack.defender_kingdom_id}
        if attack.kind is AttackKind.GAIA:
            allowed.add(next(
                (kingdom.id for kingdom in kingdoms if kingdom.is_gaia),
                GAIA_KINGDOM_KEY,
            ))
        if winner_kingdom_id not in allowed:
            raise AttackStateError("this kingdom took no part in the attack")
        attack.state = AttackState.RESOLVED
        attack.winner_kingdom_id = winner_kingdom_id
        attack.resolved_at = _now()
        if winner_kingdom_id == attack.attacker_kingdom_id:
            await self._territories.transfer(attack.territory_id, winner_kingdom_id)
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: attack on %s resolved for %s", attack.map_key, winner_kingdom_id)
        return attack

    async def restitute(self, attack_id: str) -> AttackModel:
        """Admin exception (D17): give the consumed attack budget back."""
        attack = await self._find_attack(attack_id)
        if attack.restituted:
            return attack
        lord = await self._require_lord(attack.attacker_lord_id)
        lord.attack_used = max(0, lord.attack_used - 1)
        await self._store.upsert_lord(lord.to_mongo())
        attack.restituted = True
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: attack budget restituted to %s (admin)", lord.id)
        return attack

    async def recharge_weekly_budgets(self) -> int:
        """Recharge every lord's weekly budgets at the cycle switch (D1)."""
        count = 0
        for lord in await self._kingdoms.lords():
            if lord.attack_used or lord.defense_used:
                lord.attack_used = 0
                lord.defense_used = 0
                await self._store.upsert_lord(lord.to_mongo())
                count += 1
        logger.info("kingdoms: weekly budgets recharged for %s lords", count)
        return count

    # ------------------------------------------------------------------
    # Gaïa free-for-all (§13, D6/D42/D44)
    # ------------------------------------------------------------------
    async def declare_gaia_attack(
        self,
        player_id: str,
        map_key: str,
        lobby_url: str,
        *,
        now: datetime | None = None,
    ) -> AttackModel:
        """Declare or join the Gaïa free-for-all on a Gaïa territory.

        The first declarer reserves the territory and opens the slot
        (§13.2); one lord per kingdom (D42), up to the configured
        maximum participants, everyone for himself (D6), no time limit
        (D44 — the result comes through the game contract).
        """
        season = await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        if lord.attack_used >= self._config.attacks.attacks_per_week:
            raise NoBudgetError("the weekly attack budget is spent")
        timestamp = now or _now()
        # marche-v2 art. 6: an exploring lord can neither attack nor defend.
        if lord.exploration_locked_until is not None and lord.exploration_locked_until > int(
            timestamp.timestamp()
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        territory = await self._territory_by_map(map_key)
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if not owner.is_gaia:
            raise TerritoryNotAttackableError("the free-for-all targets a Gaïa territory")
        if territory.is_protected_at(timestamp):
            raise TerritoryNotAttackableError("the territory is protected (Garde Royale/Corruption)")
        attack = next(
            (
                item
                for item in await self.attacks()
                if item.kind is AttackKind.GAIA
                and item.territory_id == territory.id
                and not item.is_over
            ),
            None,
        )
        if attack is None:
            # Volière Corbeau (voliere-v1): a « suivi » lord declares
            # with the 24h delay — the Gaïa free-for-all follows the
            # same rule as the player attacks.
            delay_hours = self._config.attacks.gaia_attack_delay_hours
            if await self._lord_tracked_by_corbeau(lord.id, timestamp):
                delay_hours = CORBEAU_TRACK_DELAY_HOURS
            # Lord's Day window: the Gaïa free-for-all follows the same
            # declare-vs-play rule as the player attacks (D44's "no
            # time limit" stays — the window only refuses an execution
            # that would land inside it).
            _refuse_lords_day_execution(
                await self._active_lords_day_window(timestamp),
                timestamp,
                delay_hours,
            )
            lord.attack_used += 1
            await self._store.upsert_lord(lord.to_mongo())
            attack = self._new_gaia_attack(season, territory, lord, lobby_url, timestamp, delay_hours)
            await self._store.upsert_attack(attack.to_mongo())
            logger.info("kingdoms: %s opens the Gaïa free-for-all on %s", lord.id, territory.map_key)
            return attack
        return await self._join_gaia_attack(attack, lord, territory)



    def _new_gaia_attack(
        self,
        season: SeasonState,
        territory: TerritoryModel,
        lord: LordModel,
        lobby_url: str,
        timestamp: datetime,
        delay_hours: int,
    ) -> AttackModel:
        """Build the first declaration of a Gaïa free-for-all."""
        if lord.kingdom_id is None:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        return AttackModel(
            _id=f"{season.id}-g-{territory.id}",
            season_id=season.id,
            kind=AttackKind.GAIA,
            territory_id=territory.id,
            map_key=territory.map_key,
            defender_kingdom_id=territory.owner_kingdom_id,
            attacker_lord_id=lord.id,
            attacker_kingdom_id=lord.kingdom_id,
            lobby_url=lobby_url.strip(),
            declared_at=timestamp,
            expires_at=timestamp + timedelta(hours=delay_hours),
            participants=[lord.id],
        )

    async def _join_gaia_attack(
        self, attack: AttackModel, lord: LordModel, territory: TerritoryModel
    ) -> AttackModel:
        """Add one lord to an open Gaïa free-for-all (D6/D42)."""
        if attack.state is not AttackState.DECLARED:
            raise AttackStateError("the free-for-all no longer accepts participants")
        if any(participant == lord.id for participant in attack.participants):
            raise AttackStateError("the lord already takes part in this free-for-all")
        lords = {item.id: item for item in await self._kingdoms.lords()}
        for participant in attack.participants:
            other = lords.get(participant)
            if other is not None and other.kingdom_id == lord.kingdom_id:
                raise GaiaAttackFullError("a kingdom joins a Gaïa free-for-all once (D42)")
        if len(attack.participants) >= self._config.attacks.gaia_max_participants:
            raise GaiaAttackFullError("the free-for-all is full")
        lord.attack_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        attack.participants = [*attack.participants, lord.id]
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: %s joins the Gaïa free-for-all on %s", lord.id, territory.map_key)
        return attack

    # ------------------------------------------------------------------
    # Combat technologies (§20 combat part, D9-D12/D18/D28/D36/D43)
    # ------------------------------------------------------------------
    async def technology_state(self, kingdom_id: str) -> TechnologyState:
        """Return (creating when absent) the kingdom's technology state."""
        for doc in await self._store.find_technologies():
            state = TechnologyState.from_mongo(doc)
            if state.kingdom_id == kingdom_id:
                return state
        return TechnologyState(_id=kingdom_id, season_id=(await self._require_season()).id)

    async def buy_technology(self, kingdom_id: str, technology: str) -> TechnologyState:
        """Buy one combat technology with tech points (D9/D36)."""
        if technology not in COMBAT_TECHNOLOGIES:
            raise UnknownTechnologyError(f"unknown combat technology: {technology}")
        cost = self._cost(technology)
        limit = self._config.technologies.limits.get(technology, 0)
        state = await self.technology_state(kingdom_id)
        if not state.can_afford(cost):
            raise InsufficientTechPointsError("the kingdom lacks tech points")
        bought = state.purchases.get(technology, 0)
        if limit > 0 and bought >= limit:
            raise TechnologyLimitReachedError("the per-season purchase limit is reached")
        if not state.spend(technology, cost, limit):
            raise TechnologyLimitReachedError("the purchase was refused")
        await self._store.upsert_technology(state.to_mongo())
        logger.info("kingdoms: %s bought %s for %s tech points", kingdom_id, technology, cost)
        return state

    async def activate_traquenard(
        self,
        player_id: str,
        target_kingdom_id: str,
        *,
        now: datetime | None = None,
    ) -> KingdomModel:
        """Activate a Traquenard against a kingdom (Drasah's recâblage v2).

        INDEPENDENT of any declaration (2026-10-14): any lord of the
        activator kingdom triggers it from the kingdom's own salon. Two
        effects — (1) one-shot: +2h of lock on every marriage ALREADY
        sealed in the target kingdom (a later marriage never is); (2)
        a 6h window (``traquenard_until``): every attack declared
        against the target while the window is open faces the Facile AI
        (level 1 whatever the defence bot's real level — the reveal
        shows 1/5). One bought charge is consumed at the activation;
        the target learns it only when an attack is actually declared.
        """
        await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        if target_kingdom_id == lord.kingdom_id:
            raise AttackError("the Traquenard targets another kingdom, never its own")
        target = await self._kingdom_by_id(target_kingdom_id)
        if target.is_gaia:
            raise TerritoryNotAttackableError("the Traquenard targets a player kingdom")
        timestamp = now or _now()
        # Volière Lâcher de faucon (voliere-v1): the Traquenard is an
        # adverse technology effect — a released target is immune.
        if _faucon_released(target, timestamp):
            raise TechnologyError("the target released the faucon — immune to technologies")
        state = await self.technology_state(lord.kingdom_id)
        bought = state.purchases.get("traquenard", 0)
        if bought <= 0:
            raise TechnologyLimitReachedError("the kingdom bought no Traquenard")
        state.purchases["traquenard"] = bought - 1
        await self._store.upsert_technology(state.to_mongo())
        now_ts = int(timestamp.timestamp())
        # (1) one-shot: +2h on the target's already-sealed marriages.
        for victim in await self._kingdoms.lords():
            if (
                victim.kingdom_id != target_kingdom_id
                or victim.left
                or victim.married_civilization is None
            ):
                continue
            current = int(victim.marriage_locked_until or 0)
            victim.marriage_locked_until = max(current, now_ts) + int(
                TRAQUENARD_LOCK_HOURS * 3600
            )
            await self._store.upsert_lord(victim.to_mongo())
        # (2) the 6h Facile window on the target kingdom.
        target.traquenard_until = now_ts + int(TRAQUENARD_WINDOW_HOURS * 3600)
        await self._store.upsert_kingdom(target.to_mongo())
        logger.info(
            "kingdoms: %s activated a Traquenard against %s (Facile AI for %sh, +2h locks)",
            lord.kingdom_id,
            target_kingdom_id,
            TRAQUENARD_WINDOW_HOURS,
        )
        return target

    async def base_ai_level(self) -> int:
        """Return the season epoch's base AI level (D18 scale, fallback 1)."""
        season = await self._kingdoms.current_season()
        if season is not None:
            key = str(getattr(season, "current_age_key", "") or "")
            epoch = next((e for e in self._config.ages if e.key == key), None)
            if epoch is not None:
                return int(epoch.gaia_ai_level)
        return 1

    async def defense_ai_level(self, kingdom_id: str) -> int:
        """Return the kingdom defence bot's AI level (marche-v2 art. 5).

        The base level is the season epoch's (D18: 1=Facile … 5=Extrême);
        each permanent Jeu d'armes purchase adds one, ceiling 5. This is
        the level an ATTACKER faces when the kingdom's bot takes the
        defense (the old Gaïa-only reading is gone — the defence bot is
        the kingdom's own, a Traquenard aside).
        """
        state = await self.technology_state(kingdom_id)
        return min(
            AI_LEVEL_CAP,
            await self.base_ai_level() + state.purchases.get("jeu_d_armes", 0),
        )

    async def sabotage_civilizations(
        self, attack_id: str, attacker_kingdom_id: str, civilizations: list[str]
    ) -> AttackModel:
        """Snipe civilizations on a kingdom-vs-kingdom attack (D12/D28).

        Kingdom-vs-kingdom only, at most two sniped civilizations per
        game; the effect is recorded on the attack for the game setup.
        """
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise SabotageError("sabotage targets a kingdom-vs-kingdom attack only")
        if attack.attacker_kingdom_id != attacker_kingdom_id:
            raise SabotageError("only the attacking kingdom sabotages")
        if attack.is_over:
            raise AttackStateError("the attack is over")
        sniped = list(attack.effects.get("sabotage", []))
        if len(sniped) + len(civilizations) > 2:
            raise SabotageError("at most two sniped civilizations per game")
        sniped = [*sniped, *civilizations]
        attack.effects = {**attack.effects, "sabotage": sniped}
        await self._store.upsert_attack(attack.to_mongo())
        return attack

    async def counter_espionage(self, attack_id: str, defender_kingdom_id: str) -> AttackModel:
        """Cancel the sabotage of an incoming attack (D11/D28)."""
        attack = await self._find_attack(attack_id)
        if attack.defender_kingdom_id != defender_kingdom_id:
            raise SabotageError("only the defending kingdom counters")
        if "sabotage" not in attack.effects:
            return attack  # nothing to counter — idempotent
        effects = {key: value for key, value in attack.effects.items() if key != "sabotage"}
        attack.effects = effects
        await self._store.upsert_attack(attack.to_mongo())
        return attack

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _cost(self, technology: str) -> int:
        """Return the configured cost of one technology (D9)."""
        return int(getattr(self._config.technologies, technology))

    async def _territory_busy(self, territory_id: str) -> bool:
        """Whether an ongoing attack targets the territory (D42)."""
        return any(
            attack.territory_id == territory_id and not attack.is_over
            for attack in await self.attacks()
        )

    async def _territory_by_map(self, map_key: str) -> TerritoryModel:
        """Resolve a territory by its map key."""
        territory = next(
            (
                item
                for item in await self._territories.territories()
                if item.map_key == map_key
            ),
            None,
        )
        if territory is None:
            raise KingdomNotFoundError("no drawn territory with this map")
        return territory

    async def _kingdom_by_id(self, kingdom_id: str) -> KingdomModel:
        """Resolve a kingdom by id."""
        kingdom = next(
            (item for item in await self._kingdoms.kingdoms() if item.id == kingdom_id),
            None,
        )
        if kingdom is None:
            raise KingdomNotFoundError("no kingdom with this id")
        return kingdom

    async def _active_lords_day_window(
        self, timestamp: datetime
    ) -> tuple[datetime, datetime] | None:
        """Return the Lord's Day window at ``timestamp``, calendar or forced.

        The real calendar window (Sunday 23:30 → Monday 10:00) applies
        first — but NEVER on the season's end Sunday (no window before
        the 23:59 verdict) nor on a finished season; the manual test
        override stored on the season
        (``lords_day_forced_start``/``lords_day_forced_end``, Drasah's
        Paramètres seam) acts as a second window when the timestamp
        falls inside it (unless the season is finished).
        """
        try:
            season = await self._kingdoms.current_season()
        except Exception:
            return None
        if season is None:
            return None
        calendar = active_lords_day_window(self._config, season, timestamp)
        if calendar is not None:
            return calendar
        if season.finished:
            return None
        start = season.lords_day_forced_start
        end = season.lords_day_forced_end
        if start is None or end is None:
            return None
        if start <= timestamp < end:
            return (start, end)
        return None

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
        return season

    async def _require_running_season(self) -> SeasonState:
        """Return the running season, refusing a finished one.

        Drasah's endcycle rule (2026-10-11): after the 23:59 verdict the
        season stays frozen — every combat action raises until the next
        launch.
        """
        season = await self._require_season()
        if season.finished:
            raise SeasonClosedError("the season is over — no combat action is possible")
        return season

    async def _require_lord(self, player_id: str) -> LordModel:
        """Return the player's lord document or raise."""
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None:
            raise NotEnrolledError("the player is not enrolled in the current season")
        return lord

    async def _find_attack(self, attack_id: str) -> AttackModel:
        """Return the attack document or raise a not-found error."""
        attack = next(
            (item for item in await self.attacks() if item.id == attack_id),
            None,
        )
        if attack is None:
            raise AttackNotFoundError("no attack with this id in the current data set")
        return attack
