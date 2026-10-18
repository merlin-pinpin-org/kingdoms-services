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
from datetime import datetime, timedelta
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


class ScoreNotReadyError(AttackStateError):
    """Raised when a score declaration comes from a player without « Prêt ».

    attaques-phases-v2 FIX: before this gate a player could declare a
    result without confirming the RDV presence and short-circuit the
    T+5 no-show line (Check 57 follow-up).
    """

    code = "KINGDOMS_SCORE_NOT_READY"
    message_key = "kingdoms.errors.score_not_ready"


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
            "the attack would be played inside the Lord's Day window "
            "(declare only if the execution lands after it closes)"
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


def _apply_sabotages(pickban: dict[str, Any]) -> dict[str, Any]:
    """Apply both sides' sabotages to the targeted picks (D77).

    A sabotaged civilization leaves the adversary's ranked picks — the
    played civ is the best-ranked NON-sabotaged pick of each side. The
    sabotages themselves stay recorded for the game setup.
    """
    result = dict(pickban)
    attacker_sabotages = list(result.get("attacker_sabotages") or [])
    defender_sabotages = list(result.get("defender_sabotages") or [])
    result["attacker_picks"] = [civ for civ in (result.get("attacker_picks") or []) if civ not in defender_sabotages]
    result["defender_picks"] = [civ for civ in (result.get("defender_picks") or []) if civ not in attacker_sabotages]
    return result


CORBEAU_TRACK_DELAY_MINUTES = 24 * 60
"""Volière Corbeau (voliere-v1, Drasah 14/10): the delay of every attack
a « suivi » lord declares — 24h instead of the configured delay, for
the whole 24h tracking window."""


def _faucon_released(kingdom: Any, timestamp: datetime) -> bool:
    """Whether the kingdom runs an active Lâcher de faucon (voliere-v1)."""
    return int(getattr(kingdom, "faucon_release_until", 0) or 0) > int(timestamp.timestamp())


def expected_score_players(attack: AttackModel) -> list[str]:
    """Return the players whose « Prêt » the meeting waits for (never the AI)."""
    players = [str(attack.attacker_lord_id)]
    if not attack.ai_defender and attack.defender_lord_id is not None:
        players.append(str(attack.defender_lord_id))
    return players


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
        if lord.attack_used >= self._config.attacks.attacks_per_week and not bypass_guard(
            f"attack budget of {lord.display_name} ({lord.attack_used})"
        ):
            raise NoBudgetError("the weekly attack budget is spent")
        if not lobby_url.strip():
            raise AttackError("the attacker must provide the game lobby link")
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
        # lieu-sacree-v1: a cathedral chantier resists every adverse
        # technology — Embuscade included.
        if territory.is_tech_protected_at(timestamp):
            raise EmbuscadeError("the territory's chantier resists the Embuscade (lieu-sacree-v1)")
        if not lobby_url.strip():
            raise AttackError("the attacker must provide the game lobby link")
        _refuse_lords_day_execution(
            await self._active_lords_day_window(timestamp),
            timestamp,
            self._config.attacks.embuscade_delay_minutes,
        )
        state = await self.technology_state(lord.kingdom_id)
        bought = state.purchases.get("embuscade", 0)
        if bought <= 0:
            raise EmbuscadeError("the kingdom bought no Embuscade")
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
        return attack

    async def _validate_attack_target(self, lord: LordModel, map_key: str, timestamp: datetime) -> TerritoryModel:
        """Run the shared declaration checks on one target territory."""
        territory = await self._territory_by_map(map_key)
        if territory.owner_kingdom_id == lord.kingdom_id:
            raise TerritoryNotAttackableError("a kingdom cannot attack its own territory")
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if owner.is_gaia:
            raise TerritoryNotAttackableError("Gaïa territories are attacked through the free-for-all")
        if territory.is_protected_at(timestamp):
            raise TerritoryNotAttackableError("the territory is protected (Garde Royale/Corruption)")
        # lieu-sacree-v1: a SACRED territory is immune to everything.
        if territory.sacred:
            raise TerritoryNotAttackableError("the territory is SACRED — immune to every attack")
        # D68: the target kingdom's daily patrol covers all its
        # territories during the bought slot (3h once Renforcée, art. 5).
        if patrouille_active_at(
            await self.technology_state(territory.owner_kingdom_id),
            timestamp,
            base_level=await self.base_ai_level(),
        ):
            raise PatrolProtectedError("the target kingdom's patrol protects its territories (D68)")
        # D59/D60: a fresh marriage locks the lord out of combat.
        if (
            lord.marriage_locked_until is not None
            and lord.marriage_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"marriage lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by a fresh marriage (D59/D60)")
        # marche-v2 art. 6: a lord sent exploring is locked out of
        # combat for the whole 48h exploration (fixed lock).
        if (
            lord.exploration_locked_until is not None
            and lord.exploration_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"exploration lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        self._refuse_ffa_no_show(lord, timestamp)
        if await self._territory_busy(territory.id):
            raise TerritoryBusyError("the territory already has an ongoing attack")
        return territory

    def _refuse_ffa_no_show(self, lord: LordModel, timestamp: datetime) -> None:
        """ffa-exploration-v1: a missed FFA locks the lord out of combat."""
        if (
            lord.ffa_locked_until is not None
            and lord.ffa_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"ffa no-show lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by a missed FFA (ffa-exploration-v1)")

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

    async def respond_defense(
        self, attack_id: str, player_id: str, *, now: datetime | None = None
    ) -> AttackModel:
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
            raise AttackStateError("a Gaïa free-for-all has no defending side")
        if attack.state is not AttackState.DECLARED:
            raise AttackStateError("the attack no longer waits for a defender")
        timestamp = now or _now()
        cutoff = attack.expires_at - timedelta(minutes=self._config.attacks.defense_cutoff_minutes)
        if timestamp >= cutoff and not bypass_guard("defense window closing (T-15)"):
            raise DefenseCutoffError(
                "la fenêtre de défense est fermée (T-15) — le reveal a verrouillé la partie"
            )
        lord = await self._require_lord(player_id)
        if lord.kingdom_id != attack.defender_kingdom_id or lord.left:
            raise AttackStateError("only a lord of the defending kingdom can defend")
        # D59/D60: a fresh marriage locks the lord out of combat.
        if (
            lord.marriage_locked_until is not None
            and lord.marriage_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"marriage lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by a fresh marriage (D59/D60)")
        # marche-v2 art. 6: the exploring lord cannot defend either.
        if (
            lord.exploration_locked_until is not None
            and lord.exploration_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"exploration lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        self._refuse_ffa_no_show(lord, timestamp)
        if lord.defense_used >= self._config.attacks.defenses_per_week and not bypass_guard(
            f"defense budget of {lord.display_name} ({lord.defense_used})"
        ):
            raise NoBudgetError("the weekly defense budget is spent")
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
        if season is not None and season.finished:
            return []
        due: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.DEFENDED:
                continue
            if attack.pickban.get("sent") or attack.is_over:
                continue
            if not attack.revealed or timestamp >= attack.expires_at:
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
        hand = await self._side_hand(attack.attacker_kingdom_id if side == "attacker" else attack.defender_kingdom_id)
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
        picker = await self._require_lord(player_id)
        await chronicle.record(
            picker,
            chronicle.KIND_PICKBAN,
            now=now or _now(),
            store=self._store,
            map=attack.map_key,
            side=side,
        )
        logger.info("kingdoms: pick & ban submitted by %s on the attack on %s", player_id, attack.map_key)
        return attack

    async def validate_pickban(self, attack_id: str, player_id: str, *, now: datetime | None = None) -> AttackModel:
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
            pickban = _apply_sabotages(await self._void_faucon_sabotages(attack, pickban, now or _now()))
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
            raise PickBanStateError("le draft n'est pas encore ouvert (il part au reveal T-15)")
        if timestamp >= attack.expires_at:
            raise PickBanStateError("l'heure du RDV est atteinte — le draft est fermé")
        return attack

    async def _pick_ban_side(self, attack: AttackModel, player_id: str) -> str:
        """Resolve which side the picking player speaks for (D77)."""
        lord = await self._require_lord(player_id)
        if lord.kingdom_id == attack.attacker_kingdom_id and lord.id == attack.attacker_lord_id:
            return "attacker"
        if lord.kingdom_id == attack.defender_kingdom_id and lord.id == attack.defender_lord_id:
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
        whose pick & ban never resolved is anti-blocking defaulted
        (Drasah 18/10): random civs, no sabotage, never a forfeit —
        the RDV then waits for the game result (D20).
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
                # The defense window [declaration, T-5] closed with no defender:
                # the bot AI takes the defense AT the meeting time and
                # the RDV opens directly — no pick & ban ever left (the
                # defense side had no human to pick, and the attacker's
                # DM window is gone; the game decides, D20).
                attack.revealed = True
                attack.ai_defender = True
                attack.state = AttackState.DEFENDED
                # marche-v2 art. 2/5: the level the attacker faces — a
                # Traquenard forces Facile (1), otherwise the defence
                # bot's own level (epoch base + Jeu d'armes, ceiling 5).
                attack.effects["ai_level"] = (
                    1 if attack.traquenard else await self.defense_ai_level(attack.defender_kingdom_id)
                )
                attack.pickban = {
                    **attack.pickban,
                    "sent": True,
                    "defender_validated": True,
                    "resolved_pickban": True,
                }
                logger.info(
                    "kingdoms: no defender on %s — the bot AI of %s takes the defense (level %s)",
                    attack.map_key,
                    attack.defender_kingdom_id,
                    attack.effects["ai_level"],
                )
            await self._store.upsert_attack(attack.to_mongo())
            return attack
        if attack.state is not AttackState.DEFENDED or attack.pickban.get("resolved_pickban"):
            return None
        return await self._expire_defended(attack, timestamp)

    async def _expire_defended(self, attack: AttackModel, timestamp: datetime) -> AttackModel | None:
        """Resolve the un-resolved pick & ban of an expired defense (D77)."""
        pickban = dict(attack.pickban)
        if not pickban.get("sent"):
            # A defense locked after the last pick & ban tick (the
            # [declaration, T-5] window, Drasah 18/10): no DM ever left — the
            # pick & ban is skipped and the RDV (score phase) decides,
            # no forfait.
            pickban["resolved_pickban"] = True
            attack.pickban = pickban
            await self._store.upsert_attack(attack.to_mongo())
            return None
        # Drasah 18/10 — the anti-blocking default: a side that never
        # ranked its 3 civilizations gets 3 RANDOM ones from its hand,
        # NO sabotage and NO faucon apply for anyone, and the meeting
        # proceeds (RDV decides, D20). A forgotten draft is never a
        # forfeit anymore.
        attacker_ok = bool(pickban.get("attacker_validated"))
        defender_ok = bool(pickban.get("defender_validated")) or attack.ai_defender
        if not (attacker_ok and defender_ok):
            pickban["default_picks"] = sorted(
                side for side, ok in (("attacker", attacker_ok), ("defender", defender_ok)) if not ok
            )
            pickban["sabotages"] = {}
            pickban["attacker_sabotages"] = []
            pickban["defender_sabotages"] = []
            if not attacker_ok and not pickban.get("attacker_picks"):
                pickban["attacker_picks"] = _random_picks(await self._side_hand(attack.attacker_kingdom_id))
            if (
                not attack.ai_defender
                and not defender_ok
                and not pickban.get("defender_picks")
            ):
                pickban["defender_picks"] = _random_picks(await self._side_hand(attack.defender_kingdom_id))
            pickban["attacker_validated"] = True
            pickban["defender_validated"] = True
            logger.info(
                "kingdoms: draft defaulted (random civs, no sabotage) on %s — sides %s",
                attack.map_key,
                pickban["default_picks"],
            )
        pickban = _apply_sabotages(pickban)
        pickban["resolved_pickban"] = True
        attack.pickban = pickban
        await self._store.upsert_attack(attack.to_mongo())
        return None  # defaulted at the wire — the game decides (D20)

    # ------------------------------------------------------------------
    # The RDV score phase (attaques-score-v1)
    # ------------------------------------------------------------------
    async def score_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Open the score phase at T for every resolved pick & ban.

        The tick moves the attack to the new ``MEETING`` state: the
        RDV itself. Idempotent through ``score["opened"]`` — a restart
        replays nothing. Un-resolved pick & bans are anti-blocking
        defaulted by ``_expire_defended`` (random civs, no sabotage)
        and open on the next tick.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        if await self._active_lords_day_window(timestamp) is not None:
            return []
        due: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.DEFENDED:
                continue
            if attack.is_over or not attack.pickban.get("resolved_pickban"):
                continue
            if attack.score.get("opened") or timestamp < attack.expires_at:
                continue
            score = dict(attack.score)
            score["opened"] = True
            score["ready"] = []
            # The game-start moment, epoch seconds — the 3h reminder base.
            score["opened_at"] = int(timestamp.timestamp())
            # The T+5 no-show line, epoch seconds (Mongo-proof).
            score["ready_deadline"] = int(
                (attack.expires_at + timedelta(minutes=self._config.attacks.ready_grace_minutes)).timestamp()
            )
            attack.score = score
            attack.state = AttackState.MEETING
            await self._store.upsert_attack(attack.to_mongo())
            due.append(attack)
            logger.info("kingdoms: score phase opened on %s (meeting)", attack.map_key)
        return due

    # Score-phase timings moved to config (beta-weekend-v1, Drasah
    # 17/10): ``attacks.ready_grace_minutes`` (5), ``attacks.
    # score_reminder_hours`` (3) and ``attacks.
    # score_dispute_after_reminder_hours`` (12) keep the exact Season
    # II defaults — a beta preset compresses them without touching
    # the code.

    async def dispute_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Auto-open a dispute on every result-less meeting past 15h.

        The Score phase where everyone is Prêt but nobody declares a
        result could hang forever (bug n°2, Check 59). Fifteen hours
        after the phase opened (3h reminder + 12h, decision Drasah
        16/10), the meeting is flagged ``disputed`` — the admins
        arbitrate through ``resolve_dispute`` (the console
        ``resultat`` command). Idempotent through
        ``score["auto_dispute"]`` — the tick replays it safely.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        due: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.MEETING:
                continue
            score = dict(attack.score)
            # Only the all-Prêt meetings can hang: a missing Prêt at
            # T+5 is forfeited by ready_due, so those never reach 15h.
            if not score.get("all_ready") or score.get("declarations"):
                continue
            if score.get("auto_dispute"):
                continue
            opened_at = int(score.get("opened_at") or 0)
            if not opened_at:
                continue
            deadline = opened_at + int(
                (self._config.attacks.score_reminder_hours
                 + self._config.attacks.score_dispute_after_reminder_hours)
                * 3600
            )
            if timestamp.timestamp() < deadline:
                continue
            # The reminder line passed de facto — never re-fire the DM
            # on a meeting that now carries a dispute.
            score["reminded"] = True
            score["disputed"] = True
            score["auto_dispute"] = True
            attack.score = score
            await self._store.upsert_attack(attack.to_mongo())
            due.append(attack)
            logger.warning(
                "kingdoms: auto-opened score dispute on %s (no result 15h after opening)",
                attack.map_key,
            )
        return due

    async def mark_ready(self, attack_id: str, player_id: str, *, now: datetime | None = None) -> AttackModel:
        """Record one player's « Prêt » click in the score phase.

        Idempotent — a double click adds nothing. Only the two players
        of the meeting confirm; the AI defender never does. The
        attaque-phases-v2 early close: as soon as EVERY expected
        player is Prêt the score phase is « open » right away
        (``all_ready``) — the T+5 deadline stays the forfeit line, it
        is never a minimum wait (Drasah 15/10).
        """
        attack = await self._require_meeting(attack_id)
        self._require_score_player(attack, player_id)
        timestamp = now or _now()
        ready = list(attack.score.get("ready") or [])
        score = dict(attack.score)
        if player_id not in ready:
            ready.append(player_id)
            score["ready"] = ready
        if not score.get("all_ready") and set(ready) >= set(expected_score_players(attack)):
            score["all_ready"] = int(timestamp.timestamp())
            logger.info(
                "kingdoms: everyone is ready on the meeting of %s — score phase open",
                attack.map_key,
            )
        attack.score = score
        await self._store.upsert_attack(attack.to_mongo())
        return attack

    async def ready_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Apply the T+5 no-show line of every open meeting.

        A player without « Prêt » at T+5 loses the meeting (the other
        camp wins, immediate resolution). Both human players missing →
        double forfait: the territory stays with the defender (the
        attack ends ``expired``, no transfer). Vs AI the attacker is
        the only one who can no-show. The tick replays it safely —
        resolved meetings leave the MEETING state.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        if await self._active_lords_day_window(timestamp) is not None:
            return []
        handled: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.MEETING:
                continue
            deadline = int(attack.score.get("ready_deadline") or 0)
            if timestamp.timestamp() < deadline:
                continue
            ready = set(attack.score.get("ready") or [])
            attacker_ready = attack.attacker_lord_id in ready
            defender_needed = not attack.ai_defender and attack.defender_lord_id is not None
            defender_ready = attack.defender_lord_id in ready
            if attacker_ready and (defender_ready or not defender_needed):
                continue  # both present — the game decides
            if not attacker_ready and defender_ready:
                await self._apply_noshow(attack, attack.defender_kingdom_id, "attacker", timestamp)
            elif attacker_ready:
                await self._apply_noshow(attack, attack.attacker_kingdom_id, "defender", timestamp)
            elif not defender_needed:
                # vs AI: only the attacker confirms — he no-showed.
                await self._apply_noshow(attack, attack.defender_kingdom_id, "attacker", timestamp)
            else:
                # double no-show: double forfait, no transfer (Drasah).
                score = dict(attack.score)
                score["noshow"] = "both"
                attack.score = score
                attack.state = AttackState.EXPIRED
                attack.resolved_at = timestamp
                await self._store.upsert_attack(attack.to_mongo())
                logger.warning(
                    "kingdoms: double no-show on %s — double forfait, territory kept",
                    attack.map_key,
                )
            handled.append(attack)
        return handled

    async def _apply_noshow(self, attack: AttackModel, winner: str, noshow_side: str, timestamp: datetime) -> None:
        """Resolve a meeting by the T+5 no-show of one side."""
        score = dict(attack.score)
        score["noshow"] = noshow_side
        attack.score = score
        await self._resolve_score(attack, winner, timestamp)
        logger.warning(
            "kingdoms: %s no-showed the meeting of %s — %s wins",
            noshow_side,
            attack.map_key,
            winner,
        )

    async def score_reminder_due(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Flag every 3h-old result-less meeting for a reminder DM.

        attaques-phases-v2 (Drasah 15/10): three hours after the score
        phase opened with no declared result, both players get a DM
        reminder — the panel stays active, there is NO auto-forfeit.
        Idempotent through ``score["reminded"]`` — the tick replays it
        safely, the DM leaves only once.
        """
        timestamp = now or _now()
        season = await self._kingdoms.current_season()
        if season is not None and season.finished:
            return []
        due: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.kind is not AttackKind.PLAYER or attack.state is not AttackState.MEETING:
                continue
            score = dict(attack.score)
            opened_at = int(score.get("opened_at") or 0)
            if not opened_at or score.get("reminded") or score.get("declarations"):
                continue
            if timestamp.timestamp() < opened_at + int(self._config.attacks.score_reminder_hours * 3600):
                continue
            score["reminded"] = True
            attack.score = score
            await self._store.upsert_attack(attack.to_mongo())
            due.append(attack)
            logger.info("kingdoms: 3h score reminder due on %s", attack.map_key)
        return due

    async def declare_score(
        self,
        attack_id: str,
        player_id: str,
        winner_kingdom_id: str,
        game_id: str,
        *,
        now: datetime | None = None,
    ) -> tuple[AttackModel, str]:
        """Declare the game result of a meeting (attaques-score-v1).

        Returns ``(attack, outcome)`` where outcome is ``"resolved"``
        (the result was applied — consensus or the unilatéral fait-foi
        rule, vs AI included), ``"already"`` (the same result of an
        already resolved attack — idempotent) or ``"disputed"`` (a
        contrary declaration after the resolution: the score phase
        flags ``disputed`` and the admins arbitrate — see
        ``resolve_dispute``).
        """
        await self._require_running_season()
        timestamp = now or _now()
        if await self._active_lords_day_window(timestamp) is not None and not bypass_guard("lords-day score window"):
            raise LordsDayAttackError(
                "the result cannot be declared inside the Lord's Day window (declare it after the window closes)"
            )
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("the free-for-all has no score phase")
        game_id = (game_id or "").strip()
        if not game_id:
            raise AttackError("the game id is required")
        allowed = {attack.attacker_kingdom_id, attack.defender_kingdom_id}
        if winner_kingdom_id not in allowed:
            raise AttackStateError("this kingdom took no part in the attack")
        if attack.is_over:
            score = dict(attack.score)
            declarations = dict(score.get("declarations") or {})
            declarations[player_id] = {
                "winner": winner_kingdom_id,
                "game_id": game_id,
                "at": int(timestamp.timestamp()),
            }
            score["declarations"] = declarations
            score["game_id"] = game_id
            if attack.winner_kingdom_id == winner_kingdom_id:
                await self._store.upsert_attack(attack.to_mongo())
                return attack, "already"
            score["disputed"] = True
            attack.score = score
            await self._store.upsert_attack(attack.to_mongo())
            logger.warning(
                "kingdoms: DISPUTE on %s — %s declares %s after the %s resolution",
                attack.map_key,
                player_id,
                winner_kingdom_id,
                attack.winner_kingdom_id,
            )
            return attack, "disputed"
        if attack.state is not AttackState.MEETING:
            raise AttackStateError("the score phase is not open on this attack")
        self._require_score_player(attack, player_id)
        # attaques-phases-v2 FIX (gate Prêt): a player without « Prêt »
        # cannot declare — the T+5 no-show line cannot be short-circuited
        # by a fast declaration anymore (Check 57 follow-up).
        if player_id not in (attack.score.get("ready") or []):
            raise ScoreNotReadyError(
                "confirm your presence at the meeting first (the Prêt button) before declaring a result"
            )
        score = dict(attack.score)
        declarations = dict(score.get("declarations") or {})
        declarations[player_id] = {
            "winner": winner_kingdom_id,
            "game_id": game_id,
            "at": int(timestamp.timestamp()),
        }
        score["declarations"] = declarations
        score["game_id"] = game_id
        attack.score = score
        # The FIRST declaring player's result FAIT FOI (Drasah 15/10) —
        # consensus is only the happy case where both agree; a contrary
        # declaration lands AFTER the resolution as a dispute.
        await self._resolve_score(attack, winner_kingdom_id, timestamp)
        logger.info(
            "kingdoms: result declared on %s by %s (game %s) — %s wins",
            attack.map_key,
            player_id,
            game_id,
            winner_kingdom_id,
        )
        return attack, "resolved"

    async def _resolve_score(self, attack: AttackModel, winner_kingdom_id: str, timestamp: datetime) -> None:
        """Apply the game result of a meeting (D20), idempotently."""
        attack.state = AttackState.RESOLVED
        attack.winner_kingdom_id = winner_kingdom_id
        attack.resolved_at = timestamp
        if winner_kingdom_id == attack.attacker_kingdom_id:
            await self._apply_attacker_win(attack)
        await self._store.upsert_attack(attack.to_mongo())
        await self._record_verdicts(attack)

    async def _require_meeting(self, attack_id: str) -> AttackModel:
        """Fetch the attack and enforce the open score phase."""
        attack = await self._find_attack(attack_id)
        if attack.state is not AttackState.MEETING:
            raise AttackStateError("the score phase is not open on this attack")
        return attack

    def _require_score_player(self, attack: AttackModel, player_id: str) -> str:
        """Resolve which side the declaring player speaks for."""
        if player_id == attack.attacker_lord_id:
            return "attacker"
        if player_id == attack.defender_lord_id and not attack.ai_defender:
            return "defender"
        raise AttackStateError("only the two players of the meeting declare a result")

    async def resolve_dispute(self, attack_id: str, admin_choice: str, *, now: datetime | None = None) -> AttackModel:
        """Admin arbitration of a disputed meeting (attaques-score-v1).

        ``admin_choice`` is ``"attacker"``, ``"defender"`` (the side
        that actually won) or ``"both"`` (double forfait). Best-effort
        override: a reversed attacker win gives the territory back,
        a new attacker win applies the usual chantier/sacré rules —
        the chantier case cannot be undone (logged limitation).
        """
        timestamp = now or _now()
        attack = await self._find_attack(attack_id)
        if not attack.score.get("disputed"):
            raise AttackStateError("this attack carries no dispute")
        if admin_choice not in ("attacker", "defender", "both"):
            raise AttackError("admin_choice must be attacker, defender or both")
        score = dict(attack.score)
        score["admin_choice"] = admin_choice
        score["disputed"] = False
        attack.score = score
        territory = next(
            (item for item in await self._territories.territories() if item.id == attack.territory_id),
            None,
        )
        if admin_choice == "both":
            previous = attack.winner_kingdom_id
            attack.state = AttackState.EXPIRED
            attack.winner_kingdom_id = None
            attack.resolved_at = timestamp
            if (
                territory is not None
                and previous == attack.attacker_kingdom_id
                and territory.owner_kingdom_id == previous
            ):
                await self._territories.transfer(attack.territory_id, attack.defender_kingdom_id)
            await self._store.upsert_attack(attack.to_mongo())
            logger.warning(
                "kingdoms: admin arbitration on %s — double forfait, territory kept",
                attack.map_key,
            )
            return attack
        winner = attack.attacker_kingdom_id if admin_choice == "attacker" else attack.defender_kingdom_id
        if attack.winner_kingdom_id == winner:
            await self._store.upsert_attack(attack.to_mongo())  # confirm the resolution
            logger.warning(
                "kingdoms: admin arbitration on %s confirmed the %s win",
                attack.map_key,
                winner,
            )
            return attack
        attack.winner_kingdom_id = winner
        attack.state = AttackState.RESOLVED
        attack.resolved_at = timestamp
        if winner == attack.attacker_kingdom_id:
            if territory is not None and territory.is_chantier_at(timestamp):
                logger.warning(
                    "kingdoms: admin arbitration on %s hits a CHANTIER — cannot be undone",
                    attack.map_key,
                )
            await self._apply_attacker_win(attack)
        else:
            if territory is not None and territory.owner_kingdom_id == attack.attacker_kingdom_id:
                await self._territories.transfer(attack.territory_id, attack.defender_kingdom_id)
        await self._store.upsert_attack(attack.to_mongo())
        logger.warning(
            "kingdoms: admin arbitration on %s overrode the result — %s wins",
            attack.map_key,
            winner,
        )
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
            if attack.state not in (
                AttackState.DECLARED,
                AttackState.DEFENDED,
                AttackState.MEETING,
            ):
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
        if await self._active_lords_day_window(_now()) is not None and not bypass_guard("lords-day resolve window"):
            raise LordsDayAttackError(
                "the attack cannot be played inside the Lord's Day window (resolve it after the window closes)"
            )
        attack = await self._find_attack(attack_id)
        if attack.state is AttackState.RESOLVED:
            if attack.winner_kingdom_id == winner_kingdom_id:
                return attack  # idempotent replay of the same result
            raise AttackStateError("the attack is already resolved with another winner")
        # A Gaïa free-for-all has no defending step (D44): it resolves
        # from DECLARED once the game result comes in. A MEETING (the
        # RDV, attaques-score-v1) resolves from the score phase.
        allowed_states: tuple[AttackState, ...] = (
            AttackState.DEFENDED,
            AttackState.MEETING,
            AttackState.EXPIRED,
        )
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
            allowed.add(
                next(
                    (kingdom.id for kingdom in kingdoms if kingdom.is_gaia),
                    GAIA_KINGDOM_KEY,
                )
            )
        if winner_kingdom_id not in allowed:
            raise AttackStateError("this kingdom took no part in the attack")
        attack.state = AttackState.RESOLVED
        attack.winner_kingdom_id = winner_kingdom_id
        attack.resolved_at = _now()
        if winner_kingdom_id == attack.attacker_kingdom_id:
            await self._apply_attacker_win(attack)
        await self._store.upsert_attack(attack.to_mongo())
        await self._record_verdicts(attack)
        logger.info("kingdoms: attack on %s resolved for %s", attack.map_key, winner_kingdom_id)
        return attack

    async def _record_verdicts(self, attack: AttackModel) -> None:
        """Chronicle the verdict on BOTH clickers (Seigneurie V2).

        Drasah's fundamental rule inverted: the RESULT shows on the
        ATTACKER's and the DEFENDER's own fiches — the attacker
        clicked « déclarer », the defender clicked « défendre »; the
        verdict is a fact of those clicked actions, never of the
        kingdom as a whole.
        """
        timestamp = attack.resolved_at or _now()
        attacker_won = attack.winner_kingdom_id == attack.attacker_kingdom_id
        lords = await self._kingdoms.lords()
        attacker = next((one for one in lords if one.id == attack.attacker_lord_id), None)
        if attacker is not None:
            await chronicle.record(
                attacker,
                chronicle.KIND_ATTACK_RESULT,
                now=timestamp,
                store=self._store,
                map=attack.map_key,
                outcome="victoire" if attacker_won else "defaite",
            )
        if attack.defender_lord_id is not None:
            defender = next((one for one in lords if one.id == attack.defender_lord_id), None)
            if defender is not None:
                await chronicle.record(
                    defender,
                    chronicle.KIND_DEFENSE_RESULT,
                    now=timestamp,
                    store=self._store,
                    map=attack.map_key,
                    outcome="victoire" if not attacker_won else "defaite",
                )

    async def _apply_attacker_win(self, attack: AttackModel) -> None:
        """Apply the attacker's verdict on the attacked territory (D20).

        lieu-sacree-v1 (Drasah 14/10): a victory on a CHANTIER
        territory destroys ONLY the chantier — the territory stays with
        its defender, the attacker banks 2 ⚙️ and must RE-ATTACK to
        conquer. A SACRED territory never transfers (defensive — it
        cannot be attacked at all).
        """
        territory = next(
            (item for item in await self._territories.territories() if item.id == attack.territory_id),
            None,
        )
        resolved_at = attack.resolved_at or _now()
        if territory is not None and territory.is_chantier_at(resolved_at):
            await self._destroy_cathedral(attack, territory, resolved_at)
            return
        if territory is not None and territory.sacred:
            logger.info(
                "kingdoms: %s kept — the territory is SACRED",
                attack.map_key,
            )
            return
        await self._territories.transfer(attack.territory_id, attack.attacker_kingdom_id)

    async def _destroy_cathedral(
        self,
        attack: AttackModel,
        territory: TerritoryModel,
        now: datetime,
    ) -> None:
        """Destroy the chantier of a defeated Lieu Sacrée (lieu-sacree-v1).

        The territory itself is NOT conquered (Drasah's correction
        14/10): it stays with the defender, the attacker banks the
        destroy reward (2 ⚙️), and the cathedral auto-rebuilds on
        ANOTHER territory of the defender — or is lost when none
        exists (repurchase 3 ⚙️).
        """
        settings = self._config.parish
        territory.cathedral_until = None
        await self._store.upsert_territory(territory.to_mongo())
        attacker = await self._kingdom_by_id(attack.attacker_kingdom_id)
        attacker.tech_points_bank += settings.cathedral_destroy_reward
        await self._store.upsert_kingdom(attacker.to_mongo())
        rebuilt = next(
            (
                item
                for item in await self._territories.territories()
                if item.owner_kingdom_id == attack.defender_kingdom_id
                and item.id != territory.id
                and not item.sacred
                and not item.is_chantier_at(now)
            ),
            None,
        )
        if rebuilt is not None:
            rebuilt.cathedral_until = now + timedelta(hours=settings.cathedral_chantier_hours)
            await self._store.upsert_territory(rebuilt.to_mongo())
        logger.info(
            "kingdoms: chantier of %s destroyed by %s (+%d tech for the attacker, %s)",
            territory.map_key,
            attack.attacker_kingdom_id,
            settings.cathedral_destroy_reward,
            f"rebuilt on {rebuilt.map_key}" if rebuilt is not None else "cathedral LOST",
        )

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
        await chronicle.record(
            lord,
            chronicle.KIND_RESTITUTION,
            now=_now(),
            store=self._store,
            map=attack.map_key,
        )
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
        if lord.attack_used >= self._config.attacks.attacks_per_week and not bypass_guard(
            f"attack budget of {lord.display_name} ({lord.attack_used})"
        ):
            raise NoBudgetError("the weekly attack budget is spent")
        timestamp = now or _now()
        # marche-v2 art. 6: an exploring lord can neither attack nor defend.
        if (
            lord.exploration_locked_until is not None
            and lord.exploration_locked_until > int(timestamp.timestamp())
            and not bypass_guard(f"exploration lock of {lord.display_name}")
        ):
            raise LordLockedError("the lord is locked by the exploration (marche-v2 art. 6)")
        self._refuse_ffa_no_show(lord, timestamp)
        territory = await self._territory_by_map(map_key)
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if not owner.is_gaia:
            raise TerritoryNotAttackableError("the free-for-all targets a Gaïa territory")
        if territory.is_protected_at(timestamp):
            raise TerritoryNotAttackableError("the territory is protected (Garde Royale/Corruption)")
        if territory.sacred:
            raise TerritoryNotAttackableError("the territory is SACRED — immune to every attack")
        attack = next(
            (
                item
                for item in await self.attacks()
                if item.kind is AttackKind.GAIA and item.territory_id == territory.id and not item.is_over
            ),
            None,
        )
        if attack is None:
            # Volière Corbeau (voliere-v1): a « suivi » lord declares
            # with the 24h delay — the Gaïa free-for-all follows the
            # same rule as the player attacks.
            delay_minutes = self._config.attacks.gaia_attack_delay_minutes
            if await self._lord_tracked_by_corbeau(lord.id, timestamp):
                delay_minutes = CORBEAU_TRACK_DELAY_MINUTES
            # Lord's Day window: the Gaïa free-for-all follows the same
            # declare-vs-play rule as the player attacks (D44's "no
            # time limit" stays — the window only refuses an execution
            # that would land inside it).
            _refuse_lords_day_execution(
                await self._active_lords_day_window(timestamp),
                timestamp,
                delay_minutes,
            )
            lord.attack_used += 1
            await self._store.upsert_lord(lord.to_mongo())
            attack = self._new_gaia_attack(season, territory, lord, lobby_url, timestamp, delay_minutes)
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
        delay_minutes: int,
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
            expires_at=timestamp + timedelta(minutes=delay_minutes),
            participants=[lord.id],
        )

    async def _join_gaia_attack(self, attack: AttackModel, lord: LordModel, territory: TerritoryModel) -> AttackModel:
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
        bypass = bypass_guard(
            f"technology purchase {technology} of {kingdom_id} "
            f"(bank {state.tech_points}, {state.purchases.get(technology, 0)}/{limit})"
        )
        if not state.can_afford(cost) and not bypass:
            raise InsufficientTechPointsError("the kingdom lacks tech points")
        bought = state.purchases.get(technology, 0)
        if limit > 0 and bought >= limit and not bypass:
            raise TechnologyLimitReachedError("the per-season purchase limit is reached")
        if bypass:
            state.tech_points = max(0, state.tech_points - cost)
            state.purchases[technology] = bought + 1
        elif not state.spend(technology, cost, limit):
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
            if victim.kingdom_id != target_kingdom_id or victim.left or victim.married_civilization is None:
                continue
            current = int(victim.marriage_locked_until or 0)
            victim.marriage_locked_until = max(current, now_ts) + int(TRAQUENARD_LOCK_HOURS * 3600)
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
        return any(attack.territory_id == territory_id and not attack.is_over for attack in await self.attacks())

    async def _territory_by_map(self, map_key: str) -> TerritoryModel:
        """Resolve a territory by its map key."""
        territory = next(
            (item for item in await self._territories.territories() if item.map_key == map_key),
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

    async def _active_lords_day_window(self, timestamp: datetime) -> tuple[datetime, datetime] | None:
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
