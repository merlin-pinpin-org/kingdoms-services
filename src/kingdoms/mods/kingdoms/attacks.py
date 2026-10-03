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
from typing import TYPE_CHECKING

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
    "patrouille",
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


def _now() -> datetime:
    """Return the current UTC time (seam for deterministic tests)."""
    return datetime.now(tz=UTC)


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

    async def declare_attack(
        self,
        player_id: str,
        map_key: str,
        lobby_url: str,
        *,
        now: datetime | None = None,
    ) -> AttackModel:
        """Declare a kingdom-vs-kingdom attack on an adversary territory.

        Any lord declares (D27), the target must be free of any ongoing
        attack (D42), the attacker must have an attack budget left, and
        the declared territory owner must be another player kingdom.
        """
        season = await self._require_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        territory = await self._territory_by_map(map_key)
        if territory.owner_kingdom_id == lord.kingdom_id:
            raise TerritoryNotAttackableError("a kingdom cannot attack its own territory")
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if owner.is_gaia:
            raise TerritoryNotAttackableError("Gaïa territories are attacked through the free-for-all")
        if await self._territory_busy(territory.id):
            raise TerritoryBusyError("the territory already has an ongoing attack")
        if lord.attack_used >= self._config.attacks.attacks_per_week:
            raise NoBudgetError("the weekly attack budget is spent")
        if not lobby_url.strip():
            raise AttackError("the attacker must provide the game lobby link")
        lord.attack_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        timestamp = now or _now()
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
            expires_at=timestamp
            + timedelta(hours=self._config.attacks.player_attack_delay_hours),
        )
        await self._store.upsert_attack(attack.to_mongo())
        logger.info(
            "kingdoms: %s declared an attack on %s (expires %s)",
            lord.id,
            territory.map_key,
            attack.expires_at.isoformat(),
        )
        return attack

    async def respond_defense(self, attack_id: str, player_id: str) -> AttackModel:
        """Answer an attack: any lord of the defending kingdom (D7)."""
        attack = await self._find_attack(attack_id)
        if attack.kind is not AttackKind.PLAYER:
            raise AttackStateError("a Gaïa free-for-all has no defending side")
        if attack.state is not AttackState.DECLARED:
            raise AttackStateError("the attack no longer waits for a defender")
        lord = await self._require_lord(player_id)
        if lord.kingdom_id != attack.defender_kingdom_id or lord.left:
            raise AttackStateError("only a lord of the defending kingdom can defend")
        if lord.defense_used >= self._config.attacks.defenses_per_week:
            raise NoBudgetError("the weekly defense budget is spent")
        lord.defense_used += 1
        await self._store.upsert_lord(lord.to_mongo())
        attack.state = AttackState.DEFENDED
        attack.defender_lord_id = lord.id
        await self._store.upsert_attack(attack.to_mongo())
        logger.info("kingdoms: %s defends the attack on %s", lord.id, attack.map_key)
        return attack

    async def expire_stale(self, *, now: datetime | None = None) -> list[AttackModel]:
        """Apply the no-defense outcome to every expired declared attack (D8).

        ``auto_victory``: the attacker captures the territory (D17 keeps
        the attack consumed). ``vs_ai``: the attack moves to ``expired``
        and waits for the game result against Gaïa's AI — the resolve
        step then decides the capture.
        """
        timestamp = now or _now()
        expired: list[AttackModel] = []
        for attack in await self.attacks():
            if attack.state is not AttackState.DECLARED or attack.expires_at > timestamp:
                continue
            if self._config.attacks.no_defense_outcome == "auto_victory":
                attack.state = AttackState.RESOLVED
                attack.winner_kingdom_id = attack.attacker_kingdom_id
                attack.resolved_at = timestamp
                await self._territories.transfer(attack.territory_id, attack.attacker_kingdom_id)
            else:
                attack.state = AttackState.EXPIRED
            await self._store.upsert_attack(attack.to_mongo())
            expired.append(attack)
            logger.info("kingdoms: attack on %s expired (%s)", attack.map_key, attack.state)
        return expired

    async def resolve(self, attack_id: str, winner_kingdom_id: str) -> AttackModel:
        """Apply the game result (D20) and transfer on capture, idempotently.

        The winner is either the attacker (capture) or the defender
        (conservation); Gaïa can also keep its territory in a
        free-for-all nobody won.
        """
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
        season = await self._require_season()
        lord = await self._require_lord(player_id)
        if lord.kingdom_id is None or lord.left:
            raise NotEnrolledError("the player belongs to no kingdom of the current season")
        if lord.attack_used >= self._config.attacks.attacks_per_week:
            raise NoBudgetError("the weekly attack budget is spent")
        territory = await self._territory_by_map(map_key)
        owner = await self._kingdom_by_id(territory.owner_kingdom_id)
        if not owner.is_gaia:
            raise TerritoryNotAttackableError("the free-for-all targets a Gaïa territory")
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
        timestamp = now or _now()
        if attack is None:
            lord.attack_used += 1
            await self._store.upsert_lord(lord.to_mongo())
            attack = AttackModel(
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
                expires_at=timestamp
                + timedelta(hours=self._config.attacks.gaia_attack_delay_hours),
                participants=[lord.id],
            )
            await self._store.upsert_attack(attack.to_mongo())
            logger.info("kingdoms: %s opens the Gaïa free-for-all on %s", lord.id, territory.map_key)
            return attack
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

    async def effective_gaia_ai_level(
        self, kingdom_id: str, base_level: int
    ) -> int:
        """Return the AI level a kingdom faces (D18): Jeu d'armes +1, Traquenard -1.

        Permanent Jeu d'armes purchases raise the level once each
        (ceiling 5); each Traquenard lowers it by one (floor 1) until
        the attack resolves — stacking is data on the purchase counter.
        """
        state = await self.technology_state(kingdom_id)
        # D18: the permanent Jeu d'armes raises the base (ceiling 5);
        # each engaged Traquenard then lowers it (floor 1).
        level = min(5, base_level + state.purchases.get("jeu_d_armes", 0))
        level -= state.purchases.get("traquenard_active", 0)
        return max(1, level)

    async def engage_traquenard(self, kingdom_id: str) -> TechnologyState:
        """Engage a bought Traquenard: -1 AI level until the combat ends (D18).

        The purchase counter moves to ``traquenard_active`` so the effect
        expires (is cleared) when the attack resolves, and can stack.
        """
        state = await self.technology_state(kingdom_id)
        bought = state.purchases.get("traquenard", 0)
        if bought <= 0:
            raise TechnologyLimitReachedError("the kingdom bought no Traquenard")
        state.purchases["traquenard"] = bought - 1
        state.purchases["traquenard_active"] = state.purchases.get("traquenard_active", 0) + 1
        await self._store.upsert_technology(state.to_mongo())
        return state

    async def expire_traquenard(self, kingdom_id: str) -> TechnologyState:
        """Clear the engaged Traquenards when the combat ends (D18)."""
        state = await self.technology_state(kingdom_id)
        state.purchases.pop("traquenard_active", None)
        await self._store.upsert_technology(state.to_mongo())
        return state

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

    async def _require_season(self) -> SeasonState:
        """Return the running season or raise the no-season error."""
        season = await self._kingdoms.current_season()
        if season is None:
            raise NoSeasonError("no season is running")
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
