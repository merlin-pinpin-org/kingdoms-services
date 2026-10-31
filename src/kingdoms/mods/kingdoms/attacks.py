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
import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from kingdoms.mods.kingdoms import chronicle
from kingdoms.mods.kingdoms.admin_debug import bypass_guard
from kingdoms.mods.kingdoms.events import active_lords_day_window
from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    AttackKind,
    AttackModel,
    AttackState,
    PlanState,
    PlannedAttackModel,
    TechnologyState,
)
from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)

# battle-ai-rdv-v1: the AI's 30-90 s think-time delays — SystemRandom
# (the bot timers are not crypto, but ruff's S311 is right by default).
_rng = random.SystemRandom()

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


class ScoreNotReadyError(AttackStateError):
    """Legacy Prêt gate (attaques-phases-v2) — retired by the battle room.

    Drasah 18/10 v2: the forfeit now rides the REAL voice presence
    (``record_voice_join``), the Prêt button is a plain UI
    confirmation and a result declaration needs no Prêt anymore. The
    class stays importable for the historical wiring — nothing raises
    it anymore.
    """

    code = "KINGDOMS_SCORE_NOT_READY"
    message_key = "kingdoms.errors.score_not_ready"


class EmbuscadeError(AttackError):
    """Raised when the Embuscade cannot engage (D77)."""

    code = "KINGDOMS_EMBUSCADE"
    message_key = "kingdoms.errors.embuscade"


class LordEngagedError(AttackError):
    """Raised when an engaged lord tries a new combat action.

    planification-attaque-v1 (Drasah 23/10/2026): a lord who declared
    (or defends) an attack is LOCKED from the declaration until the
    result is announced — he can neither declare nor plan another
    attack, nor defend, nor use an Embuscade. The lock is per LORD: the
    other lords of his kingdom keep every action.
    """

    code = "KINGDOMS_LORD_ENGAGED"
    message_key = "kingdoms.errors.lord_engaged"


class LordTavernLockedError(AttackError):
    """Raised when a drinking lord tries a combat action (taverne-v1).

    Drasah 24/10/2026: a lord who drank at the Taverne is locked out
    of combat exactly like an engaged one — no declaration, no
    planning, no defense, no Embuscade — until the drink's lock
    (``LordModel.tavern_locked_until``) expires.
    """

    code = "KINGDOMS_LORD_TAVERN_LOCKED"
    message_key = "kingdoms.errors.lord_tavern_locked"


class PlanningError(AttackError):
    """Raised when a planned attack cannot be created (Drasah 23/10)."""

    code = "KINGDOMS_PLANNING"
    message_key = "kingdoms.errors.planning"


class PlanNotFoundError(AttackError):
    """Raised when a planned attack does not exist or is not live."""

    code = "KINGDOMS_PLAN_NOT_FOUND"
    message_key = "kingdoms.errors.plan_not_found"


PATROUILLE_SLOT_KEYS = ("patrouille_slot_1", "patrouille_slot_2")
PATROUILLE_HOURS = 2
PATROUILLE_REINFORCED_HOURS = 3
PATROUILLE_TZ = "Europe/Paris"
AI_LEVEL_CAP = 5
TRAQUENARD_LOCK_HOURS = 2
TRAQUENARD_WINDOW_HOURS = 6
# planification-attaque-v1 (Drasah 23/10): the preference order holds
# up to 3 territory choices (bascule auto on rupture).
PLANNING_CHOICES_MAX = 3


def patrouille_slots(state: TechnologyState) -> tuple[int, ...]:
    """Return the kingdom's bought patrol slots (start hours, D68)."""
    return tuple(state.purchases[key] for key in PATROUILLE_SLOT_KEYS if key in state.purchases)


def patrouille_reinforced(state: TechnologyState, base_level: int) -> bool:
    """Whether the kingdom's patrols are Renforcées (marche-v2 art. 5).

    The transformation is automatic and free: when the defence bot's AI
    reaches the ceiling (5, Extrême — the epoch base level plus one per
    permanent Jeu d'armes purchase), every patrol slot of the kingdom
    widens from 2h to 3h.
    """
    return min(AI_LEVEL_CAP, base_level + state.purchases.get("jeu_d_armes", 0)) >= AI_LEVEL_CAP


def patrouille_active_at(state: TechnologyState, now: datetime, *, base_level: int | None = None) -> bool:
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
    """Return the game clock: real now + the season's test offset.

    machine-temps-v2: the seam for deterministic tests AND the
    game-clock offset — the battle phases (T-15/T-5), locks and
    cooldowns ride the game clock, not the wall clock.
    """
    return game_now()


def _refuse_lords_day_execution(
    window: tuple[datetime, datetime] | None, timestamp: datetime, delay_minutes: int
) -> None:
    """Raise when an attack declared at ``timestamp`` would play inside the window.

    Drasah's declare-vs-play rule: declaring during the Lord's Day
    window stays possible, but only when the execution (the expiry
    deadline, ``delay_minutes`` after the declaration) lands after the
    window closes (Monday 10:00) — a game that would be played inside
    the window is refused.
    """
    if window is None:
        return
    expires = timestamp + timedelta(minutes=delay_minutes)
    if expires <= window[1] and not bypass_guard("lords-day declare window"):
        raise LordsDayAttackError(
            "l'attaque serait jouée dans la fenêtre du Jour du Seigneur "
            "(déclarez seulement si l'exécution tombe après sa fermeture)"
        )


def _random_picks(hand: list[str]) -> list[str]:
    """Return 3 random civilizations of a hand (the anti-blocking draft default).

    Drasah 18/10: a forgotten pick & ban is never blocking — the
    missing side plays 3 random civilizations of its Alliances hand
    (fewer when the hand is shorter), with no sabotage and no faucon.
    """
    pool = list(hand)
    random.shuffle(pool)
    return pool[:3]


CORBEAU_TRACK_DELAY_MINUTES = 24 * 60
"""Volière Corbeau (voliere-v1, Drasah 14/10): the delay of every attack
a « suivi » lord declares — 24h instead of the configured delay, for
the whole 24h tracking window."""


CANCEL_ATTACK_WINDOW_MINUTES = 10
"""restructuration-serveur-v1 b (Drasah 19/10): the declarant's
cancellation window after the declaration — hardcoded on purpose
(Drasah tranché : pas dans la config)."""


def _faucon_released(kingdom: Any, timestamp: datetime) -> bool:
    """Whether the kingdom runs an active Lâcher de faucon (voliere-v1)."""
    return int(getattr(kingdom, "faucon_release_until", 0) or 0) > int(timestamp.timestamp())


def expected_score_players(attack: AttackModel) -> list[str]:
    """Return the players whose « Prêt » the meeting waits for (never the AI)."""
    players = [str(attack.attacker_lord_id)]
    if not attack.ai_defender and attack.defender_lord_id is not None:
        players.append(str(attack.defender_lord_id))
    return players


DISPUTE_DM_ROUND_SECONDS = 30 * 60
"""litige-mp-v1 (Drasah 30/10): the two players get 30 minutes to
modify or confirm their declaration in DM before an admin takes over."""


def phase_label(attack: AttackModel) -> str:
    """Return the player-facing phase of an attack (attaques-phases-v2).

    The internal T-15/T-5/T/T+5 clock is never shown to a player —
    one label per window instead (Drasah 15/10): ``siege``
    (declaration + defense), ``draft`` (the defense locked, pick &
    ban), ``ready`` (the RDV presence) and ``score`` (the game and
    its declaration — or a finished attack).

    Drasah 18/10: a DECLARED attack keeps ``siege`` — the
    [declaration, T-5] defense window is open, the defend button live.
    """
    if attack.state is AttackState.MEETING:
        return "score" if attack.score.get("all_ready") else "ready"
    if attack.is_over:
        return "score"
    if getattr(attack, "revealed", False) and attack.state is not AttackState.DECLARED:
        return "draft"
    return "siege"


def _phase_deadlines(attack: AttackModel, config: KingdomsSeasonConfig) -> list[datetime]:
    """Return the upcoming player-facing deadlines of ONE live attack (tick-v1).

    The reveal cutoff (T-15) tracks the pending reveal of every state;
    DECLARED then tracks the meeting time T (the AI flip at T); DEFENDED
    tracks the draft outcome — the room opening at T once resolved, the
    anti-blocking default otherwise (T for a human defense, T + the AI
    draft grace vs the bot AI) — plus the AI's ``ai_validate_at``
    think time; MEETING tracks the AI's ``ai_ready_at`` and the T+5
    no-show line. Past deadlines stay in the list — the caller filters
    them against ``now`` (a single comparison for the whole board).
    """
    deadlines: list[datetime] = []
    if not attack.revealed:
        deadlines.append(attack.expires_at - timedelta(minutes=config.attacks.defense_cutoff_minutes))
    pickban = dict(attack.pickban)
    score = dict(attack.score)
    if attack.state is AttackState.DECLARED:
        deadlines.append(attack.expires_at)
    elif attack.state is AttackState.DEFENDED:
        if pickban.get("resolved_pickban"):
            # The room opens at T on the next score tick.
            deadlines.append(attack.expires_at)
        else:
            # The anti-blocking default: T for a human defense, T +
            # the AI draft grace vs the bot AI.
            grace = config.attacks.ai_draft_grace_minutes if attack.ai_defender else 0
            deadlines.append(attack.expires_at + timedelta(minutes=grace))
        if attack.ai_defender and pickban.get("sent") and pickban.get("ai_validate_at"):
            deadlines.append(datetime.fromtimestamp(float(pickban["ai_validate_at"]), tz=UTC))
    elif attack.state is AttackState.MEETING:
        if score.get("ai_ready_at"):
            deadlines.append(datetime.fromtimestamp(float(score["ai_ready_at"]), tz=UTC))
        ready_deadline = int(score.get("ready_deadline") or 0)
        if ready_deadline:
            deadlines.append(datetime.fromtimestamp(ready_deadline, tz=UTC))
    return deadlines


def _phase_advance_due(attack: AttackModel, timestamp: datetime) -> bool:
    """Whether the state machine can advance RIGHT NOW (attack-phases-tick-v2).

    Drasah 22/10: after the live beta report the chain steps triggered
    by an ACTION — the meeting-time AI flip arming the draft, the
    attacker validating his draft, a resolution landing past T — waited
    for the base tick (60 s under beta, 300 s stock) because they carry
    no upcoming clock: each hop of the vs-AI chain felt late. These
    steps are all SERVICE-SIDE state flips, so the loop clears them in
    exactly one pass — a false positive never spins, and a Discord-side
    failure never blocks the state (the send is best effort).

    The three actionable moments: the reveal ran and the draft DMs are
    still unsent; both sides validated and the pick & ban is still
    unresolved; the pick & ban resolved past T and the room never
    opened. A pre-reveal human defense is NEVER urgent — its DMs leave
    at the cutoff.
    """
    pickban = dict(attack.pickban)
    if attack.revealed and not pickban.get("sent"):
        return True
    if pickban.get("attacker_validated") and pickban.get("defender_validated") and not pickban.get("resolved_pickban"):
        return True
    score = dict(attack.score)
    if (
        pickban.get("resolved_pickban")
        and not score.get("opened")
        and attack.state is AttackState.DEFENDED
        and timestamp >= attack.expires_at
    ):
        return True
    return False


class AttackService:
    """The attack/defense state machine and combat economy (§11-§13)."""

    def __init__(
        self,
        store: KingdomsStore,
        config: KingdomsSeasonConfig,
        kingdoms_service: KingdomsService,
        territory_service: TerritoryService,
        mercenary: Any | None = None,
    ) -> None:
        """Store the seams: persistence, config, enrollment, territories.

        chef-mercenaire-v1 T2c (Drasah 25/10): the optional Mercenary
        service feeds the pick & ban — a holder's hand carries his
        LIVE mercenary civilization and one submission consumes one
        draft use. The seam stays OPTIONAL (no import at module level
        — mercenary.py already imports this one).
        """
        self._store = store
        self._config = config
        self._kingdoms = kingdoms_service
        self._territories = territory_service
        self._mercenary = mercenary

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

    @property
    def player_attack_delay_minutes(self) -> int:
        """The configured player-attack delay (restructuration-serveur-v1 b).

        The « ⚔️ Itinéraire » line of the declare flow reads this LIVE
        value — never a hardcoded copy (beta = 10 min, Corbeau +24 h).
        """
        return int(self._config.attacks.player_attack_delay_minutes)

    @property
    def gaia_attack_delay_minutes(self) -> int:
        """The configured Gaïa free-for-all delay (Itinéraire, live)."""
        return int(self._config.attacks.gaia_attack_delay_minutes)
    @property
    def embuscade_delay_minutes(self) -> int:
        """The configured Embuscade delay (embuscade-v1 — the mode view's live Itinéraire).

        The declare flow's mode view reads this LIVE value — never a
        hardcoded copy of the exceptional fixed itinerary (60 min official).
        """
        return int(self._config.attacks.embuscade_delay_minutes)

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
        bought cover is consumed on use. bluff-externe-v1 (Drasah
        29/10): the cover may be ANY active lord of the season —
        including a lord of a FOREIGN kingdom (the false flag adds
        tension) — EXCEPT a lord of the ATTACKED kingdom. The Traquenard v2 (Drasah
        2026-10-14) is INDEPENDENT of the declaration: the kingdom
        activates it from its own salon (see ``activate_traquenard``)
        and every attack declared against the target inside the 6h
        window faces the Facile AI — the declaration merely RECORDS the
        flag for the reveal.
        """
        season = await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("le joueur n'appartient à aucun royaume de la saison en cours")
        timestamp = now or _now()
        # planification-attaque-v1: an engaged lord (attacker or
        # defender of an unresolved attack) declares NOTHING new.
        await self._refuse_engaged(lord.id, timestamp)
        # taverne-v1: a drinking lord declares NOTHING either.
        self._refuse_tavern(lord, timestamp)
        territory = await self._validate_attack_target(lord, map_key, timestamp)
        cover = (
            await self._engage_cover(lord, cover_lord_id, territory.owner_kingdom_id)
            if cover_lord_id
            else None
        )
        if lord.attack_used >= self._config.attacks.attacks_per_week and not bypass_guard(
            f"attack budget of {lord.display_name} ({lord.attack_used})"
        ):
            raise NoBudgetError("le budget d'attaque hebdomadaire est épuisé")
        # Drasah 18/10: the lobby URL is no longer asked at the
        # declaration — it is shared at the RDV (ready phase) through
        # the meeting DM's 🔗 button. An empty URL at the declaration
        # is therefore VALID for a player attack.
        # Volière Corbeau (voliere-v1): a « suivi » lord declares with
        # the 24h delay for the whole 24h tracking window.
        delay_minutes = self._config.attacks.player_attack_delay_minutes
        if await self._lord_tracked_by_corbeau(lord.id, timestamp):
            delay_minutes = CORBEAU_TRACK_DELAY_MINUTES
        # Drasah's Lord's Day window: declaring inside the window is
        # fine ONLY when the execution (the attack's expiry deadline)
        # lands after the window closes (Monday 10:00) — a game that
        # would be played inside the window is refused.
        _refuse_lords_day_execution(
            await self._active_lords_day_window(timestamp),
            timestamp,
            delay_minutes,
        )
        lord.attack_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        target_kingdom = await self._kingdom_by_id(territory.owner_kingdom_id)
        traquenard = int(getattr(target_kingdom, "traquenard_until", 0) or 0) > int(timestamp.timestamp())
        attack = AttackModel(
            # attaques-score-v1: the epoch suffix makes the id unique —
            # a re-attack of the same territory by the same lord used
            # to OVERWRITE the previous attack document.
            _id=f"{season.id}-a-{territory.id}-{lord.id}-{int(timestamp.timestamp())}",
            season_id=season.id,
            kind=AttackKind.PLAYER,
            territory_id=territory.id,
            map_key=territory.map_key,
            defender_kingdom_id=territory.owner_kingdom_id,
            attacker_lord_id=lord.id,
            attacker_kingdom_id=lord.kingdom_id,
            lobby_url=lobby_url.strip(),
            declared_at=timestamp,
            expires_at=timestamp + timedelta(minutes=delay_minutes),
            cover_lord_id=cover,
            traquenard=traquenard,
        )
        await self._store.upsert_attack(attack.to_mongo())
        await chronicle.record(
            lord,
            chronicle.KIND_ATTACK_DECLARED,
            now=timestamp,
            store=self._store,
            map=territory.map_key,
            target=target_kingdom.name,
        )
        logger.info(
            "kingdoms: %s declared an attack on %s (expires %s%s%s)",
            lord.id,
            territory.map_key,
            attack.expires_at.isoformat(),
            " under cover" if cover else "",
            " through an active Traquenard" if traquenard else "",
        )
        # planification-attaque-v1 (Drasah 23/10): the declaration is
        # PRIORITY — it ruptures every plannification whose active
        # choice is this territory (auto-switch or break, DM event).
        await self._rupture_plans_on(territory.map_key, timestamp)
        return attack

    async def _lord_tracked_by_corbeau(self, lord_id: str, timestamp: datetime) -> bool:
        """Whether any kingdom currently « suit » this lord (voliere-v1)."""
        for kingdom in await self._kingdoms.kingdoms():
            for entry in kingdom.corbeau_tracking or []:
                if str(entry.get("lord_id")) == lord_id and int(entry.get("until", 0) or 0) > int(
                    timestamp.timestamp()
                ):
                    return True
        return False

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
            raise NotEnrolledError("le joueur n'appartient à aucun royaume de la saison en cours")
        timestamp = now or _now()
        # planification-attaque-v1: an engaged lord ambushes NOTHING.
        await self._refuse_engaged(lord.id, timestamp)
        # taverne-v1: a drinking lord ambushes NOTHING either.
        self._refuse_tavern(lord, timestamp)
        territory = await self._validate_attack_target(lord, map_key, timestamp)
        # Volière Lâcher de faucon (voliere-v1): the Embuscade is an
        # adverse technology effect — a released defender is immune.
        target_kingdom = await self._kingdom_by_id(territory.owner_kingdom_id)
        if _faucon_released(target_kingdom, timestamp):
            raise EmbuscadeError("le défenseur a relâché le faucon — immunisé aux technologies")
        # lieu-sacree-v1: a cathedral chantier resists every adverse
        # technology — Embuscade included.
        if territory.is_tech_protected_at(timestamp):
            raise EmbuscadeError("le chantier du territoire résiste à l'Embuscade (lieu-sacree-v1)")
        # planification-attaque-v1: the Embuscade button follows the
        # same flow as a classic declaration — the lobby URL is
        # shared at the RDV through the meeting DM's 🔗 button, so an
        # empty URL at the declaration is VALID here too.
        _refuse_lords_day_execution(
            await self._active_lords_day_window(timestamp),
            timestamp,
            self._config.attacks.embuscade_delay_minutes,
        )
        state = await self.technology_state(lord.kingdom_id)
        bought = state.purchases.get("embuscade", 0)
        if bought <= 0:
            raise EmbuscadeError("le royaume n'a pas acheté d'Embuscade")
        state.purchases["embuscade"] = bought - 1
        await self._store.upsert_technology(state.to_mongo())
        attack = AttackModel(
            # attaques-score-v1: epoch-unique id (see declare_attack).
            _id=f"{season.id}-a-{territory.id}-{lord.id}-{int(timestamp.timestamp())}",
            season_id=season.id,
            kind=AttackKind.PLAYER,
            territory_id=territory.id,
            map_key=territory.map_key,
            defender_kingdom_id=territory.owner_kingdom_id,
            attacker_lord_id=lord.id,
            attacker_kingdom_id=lord.kingdom_id,
            lobby_url=lobby_url.strip(),
            declared_at=timestamp,
            expires_at=timestamp + timedelta(minutes=self._config.attacks.embuscade_delay_minutes),
            embuscade=True,
        )
        await self._store.upsert_attack(attack.to_mongo())
        await chronicle.record(
            lord,
            chronicle.KIND_EMBUSCADE,
            now=timestamp,
            store=self._store,
            map=territory.map_key,
            target=target_kingdom.name,
        )
        logger.info(
            "kingdoms: %s declared an EMBUSCADE on %s (expires %s)",
            lord.id,
            territory.map_key,
            attack.expires_at.isoformat(),
        )
        # planification-attaque-v1: the Embuscade occupies the
        # territory too — the declaration primes over every plan.
        await self._rupture_plans_on(territory.map_key, timestamp)
        return attack

    # ------------------------------------------------------------------
    # Plannification (planification-attaque-v1, Drasah 23/10/2026)
    # ------------------------------------------------------------------
    async def plans(self) -> list[PlannedAttackModel]:
        """Return every planned attack of the current data set."""
        return [PlannedAttackModel.from_mongo(doc) for doc in await self._store.find_plans()]

    async def _refuse_engaged(self, player_id: str, timestamp: datetime) -> None:
        """Raise ``LordEngagedError`` when the lord is under the battle lock.

        The lock covers the attacker AND the defender of every
        unresolved attack — from the declaration to the announced
        result (Drasah 23/10): no new declaration, no plannification,
        no defense, no Embuscade. Per LORD: the other lords of the
        kingdom keep every action (the kingdom may even pose its
        technologies through them).
        """
        if bypass_guard(f"battle lock of lord {player_id}"):
            return
        for attack in await self.attacks():
            if attack.is_over:
                continue
            if str(attack.attacker_lord_id) == str(player_id) or (
                attack.defender_lord_id is not None and str(attack.defender_lord_id) == str(player_id)
            ):
                raise LordEngagedError("le seigneur est engagé dans une bataille non résolue")

    def _refuse_tavern(self, lord: LordModel, timestamp: datetime) -> None:
        """Raise ``LordTavernLockedError`` while a drink locks the lord.

        taverne-v1 (Drasah 24/10/2026): the drink reuses the battle
        lock's semantics — a drinking lord can neither declare, nor
        plan, nor defend, nor ambush until the lock expires. Per LORD,
        like every lock of the mod.
        """
        if (
            lord.tavern_locked_until is not None
            and lord.tavern_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"tavern lock of {lord.display_name}")
        ):
            raise LordTavernLockedError("le seigneur est bloqué par une boisson de taverne (taverne-v1)")

    def _plan_declare_at(self, plan: PlannedAttackModel) -> datetime:
        """The scheduled declaration moment: T - itinéraire (Drasah 23/10).

        The declaration cannot be skipped — the RDV keeps at least the
        travel delay of a fresh declaration, whatever the itinéraire
        (6h vs a player kingdom, 3h vs Gaïa, 10 min in beta — the
        config value is the single source of truth).
        """
        delay = timedelta(minutes=int(self._config.attacks.player_attack_delay_minutes))
        return plan.meeting_at - delay

    async def plan_attack(
        self,
        player_id: str,
        choices: list[str],
        meeting_at: datetime,
        *,
        now: datetime | None = None,
    ) -> PlannedAttackModel:
        """Schedule an attack: 1-3 territories in order + the RDV hour T.

        The plan reserves NOTHING (Drasah 23/10 — the declaration
        primes): an adverse declaration on the active choice ruptures
        it (auto-switch to the next choice, or break). The declaration
        itself fires at T - itinéraire through ``plan_events`` — the
        weekly budget is consumed THERE, never at the planning. The
        golden rule: T never sits further than ``planning_max_hours``,
        and never closer than now + itinéraire.
        """
        season = await self._require_running_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("le joueur n'appartient à aucun royaume de la saison en cours")
        timestamp = now or _now()
        await self._refuse_engaged(player_id, timestamp)
        # taverne-v1: a drinking lord plans NOTHING either.
        self._refuse_tavern(lord, timestamp)
        if not choices:
            raise PlanningError("un plan exige au moins un choix de territoire")
        if len(choices) > PLANNING_CHOICES_MAX:
            raise PlanningError(f"un plan retient au plus {PLANNING_CHOICES_MAX} choix")
        if len(set(choices)) != len(choices):
            raise PlanningError("le même territoire ne peut pas être retenu deux fois")
        # The golden 24h rule + the minimum itinéraire before T.
        max_horizon = timedelta(hours=int(self._config.attacks.planning_max_hours))
        travel = timedelta(minutes=int(self._config.attacks.player_attack_delay_minutes))
        if meeting_at <= timestamp + travel:
            raise PlanningError("le RDV doit tomber au moins un itinéraire plus loin")
        if meeting_at > timestamp + max_horizon:
            raise PlanningError(
                f"the RDV cannot sit further than {int(self._config.attacks.planning_max_hours)}h ahead"
            )
        # The RDV must not land inside the active Lord's Day window
        # (the execution re-checks at T - itinéraire anyway).
        window = await self._active_lords_day_window(timestamp)
        if window is not None and window[0] <= meeting_at <= window[1]:
            raise LordsDayAttackError("le RDV tomberait dans la fenêtre du Jour du Seigneur")
        # Choice 1 must be a real attackable target NOW (the others are
        # re-validated at the execution — protections may change).
        validated: list[str] = []
        for index, map_key in enumerate(choices):
            territory = await self._territory_by_map(map_key)
            if territory is None:
                raise PlanningError(f"territoire inconnu : {map_key}")
            if territory.owner_kingdom_id == lord.kingdom_id:
                raise PlanningError("un royaume ne peut pas attaquer son propre territoire")
            owner = await self._kingdom_by_id(territory.owner_kingdom_id)
            if owner.is_gaia:
                raise PlanningError("les territoires Gaïa s'attaquent par la bataille libre")
            if territory.sacred:
                raise PlanningError("le territoire est SACRÉ — immunisé contre toute attaque")
            if index == 0:
                # The active choice must be free right now — a busy
                # choice would break the plan on its very first tick.
                if await self._territory_busy(territory.id):
                    raise TerritoryBusyError("le territoire a déjà une attaque en cours")
            validated.append(territory.map_key)
        plan = PlannedAttackModel(
            _id=f"{season.id}-plan-{lord.id}-{int(timestamp.timestamp())}",
            season_id=season.id,
            lord_id=lord.id,
            kingdom_id=lord.kingdom_id,
            choices=validated,
            active_index=0,
            meeting_at=meeting_at,
            created_at=timestamp,
        )
        await self._store.upsert_plan(plan.to_mongo())
        await chronicle.record(
            lord,
            chronicle.KIND_ATTACK_DECLARED,
            now=timestamp,
            store=self._store,
            map=validated[0],
        )
        logger.info(
            "kingdoms: %s planned an attack on %s (RDV %s)",
            lord.id,
            validated[0],
            meeting_at.isoformat(),
        )
        return plan

    async def cancel_plan(self, plan_id: str, player_id: str, *, now: datetime | None = None) -> PlannedAttackModel:
        """Withdraw a planned attack — free, the plan consumed nothing."""
        plan = next((p for p in await self.plans() if str(p.id) == str(plan_id)), None)
        if plan is None or not plan.is_live:
            raise PlanNotFoundError("la plannification n'existe plus")
        if str(plan.lord_id) != str(player_id):
            raise PlanNotFoundError("seul le planificateur peut annuler son plan")
        plan.state = PlanState.CANCELLED
        plan.last_event = "cancelled"
        await self._store.upsert_plan(plan.to_mongo())
        logger.info("kingdoms: %s cancelled the plan %s", player_id, plan.id)
        return plan

    async def record_plan_dm(self, plan_id: str, channel_id: int, message_id: int) -> None:
        """Remember the récap DM refs — the panel edits THIS message."""
        plan = next((p for p in await self.plans() if str(p.id) == str(plan_id)), None)
        if plan is None:
            return
        plan.dm_channel_id = channel_id
        plan.dm_message_id = message_id
        await self._store.upsert_plan(plan.to_mongo())

    async def clear_plan_event(self, plan_id: str) -> None:
        """Clear the pending player-facing event (the DM went out)."""
        plan = next((p for p in await self.plans() if str(p.id) == str(plan_id)), None)
        if plan is None:
            return
        plan.last_event = None
        await self._store.upsert_plan(plan.to_mongo())

    async def _rupture_plans_on(self, map_key: str, timestamp: datetime) -> list[PlannedAttackModel]:
        """Break/switch every live plan whose active choice is this map.

        Called right after a successful declaration (or Embuscade): the
        declaration is PRIORITY (Drasah 23/10). Each ruptured plan
        advances to its first LATER choice that is not busy right now;
        a plan without a remaining choice breaks (DM event). Nothing is
        consumed — a plan never spent anything.
        """
        changed: list[PlannedAttackModel] = []
        for plan in await self.plans():
            if not plan.is_live or plan.active_choice != map_key:
                continue
            switched = False
            for index in range(plan.active_index + 1, len(plan.choices)):
                territory = await self._territory_by_map(plan.choices[index])
                if territory is None or await self._territory_busy(territory.id):
                    continue
                plan.active_index = index
                plan.last_event = "switched"
                plan.last_event_map = map_key
                switched = True
                break
            if not switched:
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_by_declaration"
                plan.last_event_map = map_key
            await self._store.upsert_plan(plan.to_mongo())
            changed.append(plan)
            logger.info(
                "kingdoms: plan %s ruptured on %s -> %s",
                plan.id,
                map_key,
                plan.active_choice if switched else "broken",
            )
        return changed

    async def plan_events(self, *, now: datetime | None = None) -> list[PlannedAttackModel]:
        """Play every due plan: execute, switch or break — return the changed.

        The panel tick calls this every pass: every live plan whose
        declaration moment (T - itinéraire) came due is executed on
        its ACTIVE choice through the ordinary ``declare_attack`` (the
        budget is consumed at the declaration, the JdS window is
        re-checked there, the attack salon opens through the panel);
        a choice that became unavailable switches to the next one, a
        plan without choices (or a locked/insolvent lord) breaks.
        """
        timestamp = now or _now()
        changed: list[PlannedAttackModel] = []
        for plan in await self.plans():
            if not plan.is_live:
                continue
            if self._plan_declare_at(plan) > timestamp:
                continue
            changed.extend(await self._execute_plan(plan, timestamp))
        # The rupture events of OTHER declarations (instant feedback):
        for plan in await self.plans():
            if plan.last_event and plan not in changed:
                changed.append(plan)
        return changed

    async def _execute_plan(self, plan: PlannedAttackModel, timestamp: datetime) -> list[PlannedAttackModel]:
        """Try to declare one due plan — execute, switch or break it."""
        changed: list[PlannedAttackModel] = []
        while plan.is_live and self._plan_declare_at(plan) <= timestamp:
            map_key = plan.active_choice
            if map_key is None:
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_no_choice"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                return changed
            try:
                attack = await self.declare_attack(plan.lord_id, map_key, "", now=timestamp)
            except TerritoryBusyError:
                # The active choice got declared by someone else —
                # switch to the next one (the rupture switches the
                # plan already; a stale race re-switches here).
                plan.last_event = "switched"
                plan.last_event_map = map_key
                advanced = False
                for index in range(plan.active_index + 1, len(plan.choices)):
                    territory = await self._territory_by_map(plan.choices[index])
                    if territory is None or await self._territory_busy(territory.id):
                        continue
                    plan.active_index = index
                    advanced = True
                    break
                if not advanced:
                    plan.state = PlanState.BROKEN
                    plan.last_event = "broken_by_declaration"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                if not advanced:
                    return changed
                continue
            except LordEngagedError:
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_engaged"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                return changed
            except LordTavernLockedError:
                # taverne-v1: the declaration moment landed inside a
                # drink's combat lock — the plan breaks like an
                # engagement (the DM carries the tavern note).
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_tavern"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                return changed
            except NoBudgetError:
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_budget"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                return changed
            except NotEnrolledError:
                # plan-orphan-v1 (Drasah 31/10): a plan whose lord
                # left the season (the resetup wiped the lords while
                # the plans collection survived the Mongo wipe) used
                # to RAISE here at EVERY tick — killing the whole
                # phase pass (reveals, pick & ban DMs, salon openings
                # all dead until someone re-armed the season). Break
                # the orphan instead.
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_not_enrolled"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                logger.info(
                    "kingdoms: plan %s broken — lord %s no longer enrolled",
                    plan.id,
                    plan.lord_id,
                )
                return changed
            except LordsDayAttackError:
                plan.state = PlanState.BROKEN
                plan.last_event = "broken_lords_day"
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                return changed
            except (TerritoryNotAttackableError, PatrolProtectedError) as exc:
                # Protected/patrol/sacred at the execution moment —
                # try the next choice, the protection may not cover it.
                plan.last_event = "switched"
                plan.last_event_map = map_key
                advanced = False
                for index in range(plan.active_index + 1, len(plan.choices)):
                    territory = await self._territory_by_map(plan.choices[index])
                    if territory is None or await self._territory_busy(territory.id):
                        continue
                    plan.active_index = index
                    advanced = True
                    break
                if not advanced:
                    plan.state = PlanState.BROKEN
                    plan.last_event = "broken_target"
                    plan.last_event_map = map_key
                    await self._store.upsert_plan(plan.to_mongo())
                    changed.append(plan)
                    logger.info(
                        "kingdoms: plan %s broke at execution (%s)", plan.id, exc.message
                    )
                    return changed
                await self._store.upsert_plan(plan.to_mongo())
                changed.append(plan)
                continue
            # The declaration succeeded — the plan is executed.
            plan.state = PlanState.EXECUTED
            plan.executed_attack_id = str(attack.id)
            plan.last_event = "executed"
            plan.last_event_map = map_key
            await self._store.upsert_plan(plan.to_mongo())
            changed.append(plan)
            logger.info("kingdoms: plan %s executed as attack %s", plan.id, attack.id)
            return changed
        return changed

    def _plan_probe_deadline(self, plans: list[PlannedAttackModel]) -> datetime | None:
        """The earliest upcoming declaration moment of the live plans."""
        due = [self._plan_declare_at(plan) for plan in plans if plan.is_live]
        return min(due) if due else None

    async def _validate_attack_target(self, lord: LordModel, map_key: str, timestamp: datetime) -> TerritoryModel:
        """Run the shared declaration checks on one target territory."""
        territory = await self._territory_by_map(map_key)
        if territory.owner_kingdom_id == lord.kingdom_id:
            raise TerritoryNotAttackableError("un royaume ne peut pas attaquer son propre territoire")
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if owner.is_gaia:
            raise TerritoryNotAttackableError("les territoires Gaïa s'attaquent par la bataille libre")
        if territory.is_protected_at(timestamp):
            raise TerritoryNotAttackableError("le territoire est protégé (Garde Royale/Corruption)")
        # lieu-sacree-v1: a SACRED territory is immune to everything.
        if territory.sacred:
            raise TerritoryNotAttackableError("le territoire est SACRÉ — immunisé contre toute attaque")
        # D68: the target kingdom's daily patrol covers all its
        # territories during the bought slot (3h once Renforcée, art. 5).
        if patrouille_active_at(
            await self.technology_state(territory.owner_kingdom_id),
            timestamp,
            base_level=await self.base_ai_level(),
        ):
            raise PatrolProtectedError("la patrouille du royaume cible protège ses territoires (D68)")
        # D59/D60: a fresh marriage locks the lord out of combat.
        if (
            lord.marriage_locked_until is not None
            and lord.marriage_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"marriage lock of {lord.display_name}")
        ):
            raise LordLockedError("le seigneur est bloqué par un mariage récent (D59/D60)")
        # marche-v2 art. 6: a lord sent exploring is locked out of
        # combat for the whole 48h exploration (fixed lock).
        if (
            lord.exploration_locked_until is not None
            and lord.exploration_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"exploration lock of {lord.display_name}")
        ):
            raise LordLockedError("le seigneur est bloqué par l'exploration (marche-v2 art. 6)")
        self._refuse_ffa_no_show(lord, timestamp)
        if await self._territory_busy(territory.id):
            raise TerritoryBusyError("le territoire a déjà une attaque en cours")
        return territory

    def _refuse_ffa_no_show(self, lord: LordModel, timestamp: datetime) -> None:
        """ffa-exploration-v1: a missed FFA locks the lord out of combat."""
        if (
            lord.ffa_locked_until is not None
            and lord.ffa_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"ffa no-show lock of {lord.display_name}")
        ):
            raise LordLockedError("le seigneur est bloqué par une FFA manquée (ffa-exploration-v1)")

    async def _engage_cover(
        self, lord: LordModel, cover_lord_id: str, defender_kingdom_id: str
    ) -> str:
        """Validate and consume one cover (Contre-espionnage, D77).

        bluff-externe-v1 (Drasah 29/10): the cover may be ANY active
        lord of the season — including a lord of ANOTHER kingdom (the
        false flag adds tension) — EXCEPT a lord of the ATTACKED
        kingdom (the defender must never see one of his own attack
        him) and the attacker himself.
        """
        if cover_lord_id == lord.id:
            raise AttackError("le seigneur de couverture ne peut pas être l'attaquant lui-même")
        cover = await self._require_lord(cover_lord_id)
        if cover.left or cover.kingdom_id is None:
            raise AttackError("le seigneur de couverture n'est pas un seigneur actif de la saison")
        if str(cover.kingdom_id) == str(defender_kingdom_id):
            raise AttackError("le seigneur de couverture ne peut pas appartenir au royaume attaqué")
        state = await self.technology_state(lord.kingdom_id or "")
        bought = state.purchases.get("contre_espionnage", 0)
        if bought <= 0:
            raise AttackError("le royaume n'a pas acheté de Contre-espionnage")
        state.purchases["contre_espionnage"] = bought - 1
        await self._store.upsert_technology(state.to_mongo())
        return cover.id

    async def respond_defense(self, attack_id: str, player_id: str, *, now: datetime | None = None) -> AttackModel:
        """Answer an attack: any lord of the defending kingdom (D7).

        The defense window is [declaration, T-15] (Drasah 18/10 —
        « il faut pouvoir défendre dès qu'une attaque a été déclarée,
        à la seconde près » puis « l'accès au bouclier doit être
        entre T10 et T5 »): the defense OPENS the second the attack
        is declared and CLOSES at the cutoff moment
        ``expires_at - defense_cutoff_minutes`` (the T-15 reveal
        locks the defense). Nobody answered by then and the kingdom's
        bot AI takes the defense at the meeting time instead.
        """
        await self._require_running_season()
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("une bataille libre Gaïa n'a pas de camp défenseur")
        if attack.state is not AttackState.DECLARED:
            raise AttackStateError("l'attaque n'attend plus de défenseur")
        timestamp = now or _now()
        cutoff = attack.expires_at - timedelta(minutes=self._config.attacks.defense_cutoff_minutes)
        if timestamp >= cutoff and not bypass_guard("defense window closing (T-15)"):
            raise DefenseCutoffError("la fenêtre de défense est fermée (T-15) — le reveal a verrouillé la partie")
        lord = await self._require_lord(player_id)
        if lord.kingdom_id != attack.defender_kingdom_id or lord.left:
            raise AttackStateError("seul un seigneur du royaume défenseur peut défendre")
        # planification-attaque-v1 (Drasah 23/10): an engaged lord
        # defends NOTHING else — he is locked until his own battle's
        # result is announced.
        await self._refuse_engaged(lord.id, timestamp)
        # taverne-v1: a drinking lord defends NOTHING either.
        self._refuse_tavern(lord, timestamp)
        # D59/D60: a fresh marriage locks the lord out of combat.
        if (
            lord.marriage_locked_until is not None
            and lord.marriage_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"marriage lock of {lord.display_name}")
        ):
            raise LordLockedError("le seigneur est bloqué par un mariage récent (D59/D60)")
        # marche-v2 art. 6: the exploring lord cannot defend either.
        if (
            lord.exploration_locked_until is not None
            and lord.exploration_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"exploration lock of {lord.display_name}")
        ):
            raise LordLockedError("le seigneur est bloqué par l'exploration (marche-v2 art. 6)")
        self._refuse_ffa_no_show(lord, timestamp)
        # planification-attaque-v1 (Drasah 23/10): the Embuscade
        # generates an EXCEPTIONAL defense — the defender neither
        # spends nor needs his weekly defense point against one (the
        # weekly budget covers the normal defenses only).
        if attack.embuscade:
            pass
        elif lord.defense_used >= self._config.attacks.defenses_per_week and not bypass_guard(
            f"defense budget of {lord.display_name} ({lord.defense_used})"
        ):
            raise NoBudgetError("le budget de défense hebdomadaire est épuisé")
        else:
            lord.defense_used += 1
            await self._store.upsert_lord(lord.to_mongo())
        attack.state = AttackState.DEFENDED
        attack.defender_lord_id = lord.id
        await self._store.upsert_attack(attack.to_mongo())
        await chronicle.record(
            lord,
            chronicle.KIND_DEFENSE,
            now=_now(),
            store=self._store,
            map=attack.map_key,
        )
        logger.info("kingdoms: %s defends the attack on %s", lord.id, attack.map_key)
        return attack

    async def cancel_attack(self, attack_id: str, player_id: str, *, now: datetime | None = None) -> AttackModel:
        """Withdraw a declared attack (restructuration-serveur-v1 b, Drasah 19/10).

        The DECLARANT alone, inside the 10-minute window after the
        declaration — the attacker budget is refunded IN FULL, and so
        is the defender's when a defense was already recorded. The
        T-15 reveal running (or beyond) locks the cancellation: the
        bluff has dropped, the siege is engaged. A Gaïa free-for-all
        is NEVER cancellable (axis ouvert Drasah — default followed:
        the first declarer's cancel must not rob the other lords of
        their FFA; refused until tranché).
        """
        attack = await self._find_attack(attack_id)
        if attack.is_over:
            raise AttackStateError("l'attaque est terminée")
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("une bataille libre Gaïa ne peut pas être annulée")
        if str(player_id) != str(attack.attacker_lord_id):
            raise AttackStateError("seul le déclarant peut annuler l'attaque")
        if attack.revealed and not bypass_guard("cancel window after the reveal"):
            raise AttackStateError("le reveal est passé — l'attaque ne peut plus être annulée")
        timestamp = now or _now()
        deadline = attack.declared_at + timedelta(minutes=CANCEL_ATTACK_WINDOW_MINUTES)
        if timestamp > deadline and not bypass_guard("cancel window of the declaration"):
            raise AttackStateError(f"la fenêtre d'annulation de {CANCEL_ATTACK_WINDOW_MINUTES} minutes est fermée")
        attacker = await self._require_lord(str(attack.attacker_lord_id))
        if not attack.embuscade and attacker.attack_used > 0:
            attacker.attack_used -= 1
            await self._store.upsert_lord(attacker.to_mongo())
        if attack.state is AttackState.DEFENDED and attack.defender_lord_id and not attack.ai_defender:
            defender = await self._require_lord(str(attack.defender_lord_id))
            if defender.defense_used > 0:
                defender.defense_used -= 1
                await self._store.upsert_lord(defender.to_mongo())
        # embuscade-claire-v1 (Drasah 30/10): a cancelled attack never
        # burned its cover — the Contre-espionnage returns to the stock
        # (the cancel window closes BEFORE the reveal: the cover never
        # served). The Embuscade token is another story: it stays
        # consumed for good (Drasah's anti-toxic ruling — a fake
        # ambush ping must never be free).
        if attack.cover_lord_id:
            cover_state = await self.technology_state(str(attacker.kingdom_id or ""))
            cover_state.purchases["contre_espionnage"] = (
                int(cover_state.purchases.get("contre_espionnage", 0) or 0) + 1
            )
            await self._store.upsert_technology(cover_state.to_mongo())
            logger.info(
                "kingdoms: cover of the cancelled attack %s refunded to %s",
                attack.id,
                attacker.kingdom_id,
            )
        attack.state = AttackState.CANCELLED
        attack.resolved_at = timestamp
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: attack on %s cancelled by %s", attack.map_key, player_id)
        return attack

    async def cancel_defense(self, attack_id: str, player_id: str, *, now: datetime | None = None) -> AttackModel:
        """Withdraw a recorded defense (restructuration-serveur-v1 b, Drasah 19/10).

        The DEFENDER alone, at any moment EXCEPT once the draft phase
        started — the threshold rides the SAME config source as the
        reveal/pick & ban lead (``pick_ban_lead_minutes``, Check 70;
        the beta reconfigures it — nothing is hardcoded). The defense
        budget is refunded with the same logic as the attack.
        """
        attack = await self._find_attack(attack_id)
        if attack.is_over:
            raise AttackStateError("l'attaque est terminée")
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("une bataille libre Gaïa n'a pas de camp défenseur")
        if attack.state is not AttackState.DEFENDED or not attack.defender_lord_id:
            raise AttackStateError("aucune défense n'est enregistrée sur cette attaque")
        if str(player_id) != str(attack.defender_lord_id):
            raise AttackStateError("seul le défenseur peut retirer sa défense")
        timestamp = now or _now()
        draft_start = attack.expires_at - timedelta(minutes=self._config.attacks.pick_ban_lead_minutes)
        if (attack.revealed or (attack.pickban or {}).get("sent") or timestamp >= draft_start) and not bypass_guard(
            "defense cancel after the draft start"
        ):
            raise AttackStateError("la phase de draft a commencé — la défense ne peut plus être retirée")
        defender = await self._require_lord(str(attack.defender_lord_id))
        if defender.defense_used > 0:
            defender.defense_used -= 1
            await self._store.upsert_lord(defender.to_mongo())
        attack.state = AttackState.DECLARED
        attack.defender_lord_id = None
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: defense of %s withdrawn by %s", attack.map_key, player_id)
        return attack

    async def reveal_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Run the T-15 reveal (D77): unmask the cover, close the defense.

        Drasah 18/10: the defense window is [declaration, T-15] — the
        reveal CLOSES the defense (no more answers) and, on the same
        tick, the defended attacks' pick & ban DMs leave
        (``pick_ban_due`` rides ``revealed``). Without a defender the
        bot AI still takes the defense at the meeting time
        (``expire_stale``). Idempotent through ``revealed`` — the
        scheduler tick replays it after every restart.
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
            cutoff = attack.expires_at - timedelta(minutes=self._config.attacks.defense_cutoff_minutes)
            if timestamp < cutoff:
                continue
            attack.revealed = True
            await self._store.upsert_attack(attack.to_mongo())
            revealed.append(attack)
        return revealed

    async def pick_ban_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Mark the attacks whose T-5 pick & ban DMs must leave (D77).

        Idempotent through ``pickban["sent"]``. The AI side
        auto-validates at the same moment (no human to wait for).

        Drasah 18/10: the DMs leave AT THE REVEAL (T-15), the moment
        the defense locks — the old T-5 lead window was shorter than
        the scheduler tick and the draft NEVER left (live beta 17/10:
        no DM, then double forfeit at T). The pickban rides
        ``revealed``: once the reveal ran on a defended attack, the
        DMs are due on the very same tick.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
