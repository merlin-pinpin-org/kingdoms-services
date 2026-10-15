"""Kingdoms mod season-state models.

Season data is **reset wholesale on every new season launch** (reference
§3.3/§28, decision D38/D52): these models are the per-season state the
bot manages. They never contain Discord or game IDs — identities are
platform-level concerns; the mod stores cross-platform entity keys and
resolves surfaces through the platform services.

Placement (ADR-0020): the domain core owns these collections in
svc-core; the Discord surface reads them over the gRPC seams.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

GAIA_KINGDOM_KEY = "gaia"


def _from_wire[ModelT: BaseModel](cls: type[ModelT], data: dict[str, Any]) -> ModelT:
    """Rebuild a model from a MongoDB document (wire-tolerant).

    MongoDB gives back plain strings for the StrEnum fields and lists
    for the tuple/list fields; the models are ``strict=True`` so the
    in-code constructor calls stay exact, but ``from_mongo`` must read
    the wire leniently (``strict=False``) or every stored document
    fails validation on the way back (first seen live: the starting
    draft re-reading a freshly upserted kingdom, 2026-10-10).
    """
    return TypeAdapter(cls).validate_python(data, strict=False)




class KingdomType(StrEnum):
    """Player kingdom vs the AI kingdom (Gaïa — D32)."""

    PLAYER = "player"
    GAIA = "gaia"


class LordRole(StrEnum):
    """King or Lord (reference §5)."""

    KING = "king"
    LORD = "lord"


class ChronicleEntryModel(BaseModel):
    """One FACTUAL deed of a lord's chronicle (Seigneurie V2, Drasah 15/10).

    Exactly-timestamped. Only actions the lord REALLY CLICKED are recorded — never actions
    suffered, never system events. ``params`` carries the display
    values (civ, target, map, outcome…) as strings; the fiche renders
    one FR/EN template per ``kind``.
    """

    model_config = ConfigDict(strict=True)

    at: int
    kind: str
    params: dict[str, str] = Field(default_factory=dict)


class KingdomValidation(StrEnum):
    """The admin-side validation state of a kingdom (rule 35, D70).

    A founding King creates a PENDING kingdom: it exists as data (the
    admin can inspect and rename it) but its Discord structure is only
    provisioned once the admin APPROVES it. A REFUSED kingdom keeps its
    document for the record; its members fall back to the waiting
    queue and the admin may re-decide later (corrected name).
    """

    PENDING = "pending"
    APPROVED = "approved"
    REFUSED = "refused"


class KingdomModel(BaseModel):
    """A kingdom of the current season (player or Gaïa)."""

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    type: KingdomType
    name: str
    marriage_capacity: int = Field(default=0, ge=0)
    name_approved: bool = True
    validation: KingdomValidation = KingdomValidation.APPROVED
    civilizations: list[str] = Field(default_factory=list)
    secured_civilizations: list[str] = Field(default_factory=list)
    tech_points_bank: int = Field(default=0, ge=0)
    parish_tier: int = Field(default=1, ge=1, le=3)
    """The Paroisse palier (D64): 1 Chapelle, 2 Église, 3 Cathédrale."""
    marriage_points: int = Field(default=1, ge=0)
    """Points de mariage (D59): consumable stock — one per standard marriage.

    Refilled +1 at every epoch switch and at every Lord's Day (Chapelle
    bonus, D64); the arranged marriage never consumes any (D74).
    """
    arranged_marriage_stock: int = Field(default=0, ge=0)
    """Jeton(s) de mariage arrangé bought at the Marché (3 🔬 each, D60),
    consumed at the Paroisse button — never from the marriage points."""
    traquenard_until: int | None = None
    """Fin (timestamp Unix) de la fenêtre Traquenard ACTIVE contre ce
    royaume (Drasah 2026-10-14, recâblage v2) : pendant la fenêtre l'IA
    de défense du royaume est Facile (niveau 1) pour TOUTE attaque
    adverse. La fenêtre s'ouvre à l'activation (le royaume activant
    consomme une charge, voir ``activate_traquenard``) — les verrous
    de mariage +2h, eux, sont appliqués en one-shot à ce moment-là."""
    market_registry: list[dict[str, Any]] = Field(default_factory=list)
    """Chambre des comptes (marche-v2, 2026-10-13): the kingdom's dated
    purchase ledger — one ``{"item", "detail", "cost", "at"}`` entry per
    successful Marché buy, newest readable through the ledger modal."""
    voliere_cooldowns: dict[str, int] = Field(default_factory=dict)
    """Volière (voliere-v2, 2026-10-14): per-BIRD cooldown registry,
    SHARED by the whole kingdom — one ``{bird: unix_ready_at}`` entry
    per flown bird (Drasah: ONE action and the WHOLE bird flies until
    the action's cooldown elapses; the cooldowns are par royaume, not
    per lord). The acting lord is only recorded in the admin logs."""
    voliere_slots: list[str] = Field(default_factory=list)
    """Volière (voliere-v2, 2026-10-14): the birds this kingdom chose,
    one per age slot — Féodal opens the 1st slot, Château the 2nd,
    Impérial the 3rd; a bird already owned can never be re-taken."""
    corbeau_tracking: list[dict[str, Any]] = Field(default_factory=list)
    """Volière Corbeau (voliere-v1): the lords currently « suivi » by
    this kingdom — one ``{"lord_id", "until"}`` entry per active
    tracking (24h window: every attack the tracked lord declares
    leaves with the 24h delay)."""
    faucon_release_until: int | None = None
    """Volière Faucon (voliere-v1): end (unix) of the active Lâcher de
    faucon — while it runs the kingdom suffers NO adverse technology
    effect (Traquenard, sabotages, Embuscade, Corruption)."""
    corbeau_theft: dict[str, Any] | None = None
    """Volière Corbeau (corbeau-v3, Drasah 15/10/2026): the kingdom's
    ONE active ⚙️ theft — ``None`` when no theft is running. States:
    ``pending`` (sent, execution 1h away) → ``awaiting_continue``
    (1st ⚙️ stolen, alarm ringing, 5-minute contest window, thief
    prompted) → ``settled_first`` (STOP or window elapsed: 1 ⚙️ kept)
    / ``continuing`` (thief answered CONTINUER, 2nd ⚙️ attempt in the
    window) → ``settled_both`` (2 ⚙️ kept) / ``dead`` (contested:
    corbeau dead, EVERYTHING lost, slot purged). Fields: ``id``,
    ``thief_lord_id``, ``target_kingdom_id``, ``target_kingdom_name``,
    ``execute_at``, ``contest_until``, ``state``, ``stolen``."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> KingdomModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    @property
    def is_gaia(self) -> bool:
        """Gaïa kingdoms are AI-controlled and never enrollable."""
        return self.type == KingdomType.GAIA


class LordModel(BaseModel):
    """A player of the current season: king or lord of a kingdom.

    ``married_civilization`` is the marriage target (one per lord,
    D34); ``marriage_locked_until`` is the real-time combat lock of a
    fresh marriage (D59: 24h standard, D60: 6h arranged) - a locked
    lord can neither attack nor defend while the unix timestamp is in
    the future. ``royal_marriage`` marks a KING's wedding (Drasah's
    Reine rule, 2026-10-11): the lock doubles, the kingdom gains +2
    tech points and the civilization stays secured for the whole
    season - even after a combat defeat.

    ``attack_used``/``defense_used`` are the weekly budgets consumed in
    the current cycle (1+1 per week — reference §11, recharge at the
    cycle switch, D1).
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kingdom_id: str | None = None
    role: LordRole
    display_name: str
    in_queue: bool = False
    attack_used: int = Field(default=0, ge=0)
    defense_used: int = Field(default=0, ge=0)
    married_civilization: str | None = None
    marriage_locked_until: int | None = None
    exploration_locked_until: int | None = None
    """Explorateur (marche-v2 art. 6): real-time combat lock of the lord
    sent exploring — 48h FIXED (never scaled by the Parish, unlike the
    marriage), during which the territory does not exist yet. The lock
    clears when the scheduler resolves the exploration (the map is
    drawn then), and a locked lord can neither attack nor defend."""
    royal_marriage: bool = False
    left: bool = False
    left_reason: str | None = None
    ffa_locked_until: int | None = None
    """FFA Exploration no-show lock (ffa-exploration-v1, Drasah
    14/10/2026): a lord who reserved a Saturday FFA slot and did not
    show up is locked out of combat (attacks, defenses, Gaïa
    free-for-alls) for 72h STARTING AT THE NEXT CYCLE SWITCH — the
    timestamp is computed at result ingestion and enforced exactly
    like the marriage and exploration locks."""
    insight_name: str | None = None
    """AoE II Insight pseudo (Seigneurie V2, Drasah 15/10/2026): the
    external ladder identity displayed on the fiche seigneur — the
    field is entered Merlin-side (treated as if the field exists)."""
    chronicle: list[ChronicleEntryModel] = Field(default_factory=list)
    """Lord chronique (Seigneurie V2, Drasah 15/10/2026): the FACTUAL,
    exactly-timestamped log of the actions the lord REALLY CLICKED —
    capped (oldest dropped), visible through the fiche only while a
    Corbeau ``corbeau_track`` of the CONSULTING kingdom is active."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> LordModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class TerritoryModel(BaseModel):
    """A territory of the current season: one AoE2 map with an owner.

    A map drawn once is "out" for the whole season (reference §8);
    ownership changes through the idempotent transfer primitive.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    map_key: str
    owner_kingdom_id: str
    drawn_at: datetime
    protected_until: datetime | None = None
    """Anti-attack/anti-corruption shield (D15/D37/D47): Garde Royale
    and Corruption protect a territory until the recorded instant."""

    cathedral_until: datetime | None = None
    """Lieu Sacrée chantier end (lieu-sacree-v1): while running, the
    territory resists every adverse technology but stays attackable
    by CLASSIC attacks with unlimited attempts."""

    sacred: bool = False
    """SACRED verdict (lieu-sacree-v1): a chantier that survived its
    72h — immune to everything, forever."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> TerritoryModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    def is_protected_at(self, now: datetime) -> bool:
        """Whether the anti-attack/anti-corruption shield is active (D15/D37/D47)."""
        if self.protected_until is None:
            return False
        until = self.protected_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until > now

    def is_chantier_at(self, now: datetime) -> bool:
        """Whether the Lieu Sacrée chantier still runs (lieu-sacree-v1)."""
        if self.cathedral_until is None:
            return False
        until = self.cathedral_until
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until > now

    def is_tech_protected_at(self, now: datetime) -> bool:
        """Whether adverse technologies bounce off (chantier, lieu-sacree-v1)."""
        return self.is_chantier_at(now)


class TechnologyState(BaseModel):
    """Technology state of a kingdom.

    Points and per-technology purchase counters for the current season
    (decisions D9/D36).
    """

    model_config = ConfigDict(strict=True)

    kingdom_id: str = Field(alias="_id")
    season_id: str
    tech_points: int = Field(default=0, ge=0)
    purchases: dict[str, int] = Field(default_factory=dict)

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> TechnologyState:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    def can_afford(self, cost: int) -> bool:
        """Whether the kingdom can spend ``cost`` tech points."""
        return self.tech_points >= cost

    def spend(self, technology: str, cost: int, limit: int) -> bool:
        """Spend points on a technology, enforcing its purchase limit.

        Idempotency is the caller's concern (state transitions); this
        enforces the economic invariants only: affordability and the
        per-season limit (D36). A ``limit`` of 0 means unlimited.
        """
        if not self.can_afford(cost):
            return False
        bought = self.purchases.get(technology, 0)
        if limit > 0 and bought >= limit:
            return False
        self.tech_points -= cost
        self.purchases[technology] = bought + 1
        return True


class AttackKind(StrEnum):
    """A kingdom-vs-kingdom attack or a Gaïa free-for-all (D6)."""

    PLAYER = "player"
    GAIA = "gaia"


class AttackState(StrEnum):
    """State machine of an attack (reference §12).

    ``declared`` waits for a defender until the delay expires;
    ``defended`` waits for the pick & ban (D77); ``meeting``
    (attaques-score-v1) is the RDV itself — the pick & ban resolved,
    the score phase (Prêt, declarations, dispute) is open; ``expired``
    applied the no-defense outcome; ``resolved`` carries the final
    winner (capture or conservation) and never moves again.
    """

    DECLARED = "declared"
    DEFENDED = "defended"
    MEETING = "meeting"
    EXPIRED = "expired"
    RESOLVED = "resolved"


class AttackModel(BaseModel):
    """An attack of the current season on one territory (§11-§13).

    Player attacks are 1v1 without reinforcements (D41) and never share
    a target with another ongoing attack (D42). Gaïa attacks are a
    free-for-all: the first declarer reserves the territory and the
    slot (§13.2), one lord per kingdom, up to the configured maximum
    participants (D6/D42).

    ``effects`` carries the combat-technology effects engaged on this
    attack (sabotaged civilizations, counter-espionage…), keyed by
    technology name — data for the surface and the game setup.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kind: AttackKind
    state: AttackState = AttackState.DECLARED
    territory_id: str
    map_key: str
    defender_kingdom_id: str
    attacker_lord_id: str
    attacker_kingdom_id: str
    lobby_url: str = ""
    declared_at: datetime
    expires_at: datetime
    defender_lord_id: str | None = None
    resolved_at: datetime | None = None
    winner_kingdom_id: str | None = None
    restituted: bool = False
    participants: list[str] = Field(default_factory=list)
    effects: dict[str, Any] = Field(default_factory=dict)
    cover_lord_id: str | None = None
    """Phases d'attaque (D77): the cover lord shown instead of the real
    attacker until the T-15 reveal (Contre-espionnage)."""
    revealed: bool = False
    """Whether the T-15 reveal already ran (restart-safe idempotence)."""
    ai_defender: bool = False
    """Whether the defending kingdom's bot AI took the defense (D77)."""
    embuscade: bool = False
    """Whether this attack is an Embuscade (T-1h, no weekly budget, D77)."""
    traquenard: bool = False
    """Whether the attacker engaged a Traquenard (marche-v2 art. 2): the
    AI faced becomes Facile (level 1) whatever the base level, and the
    marriages active at the declaration take +2h of lock."""
    pickban: dict[str, Any] = Field(default_factory=dict)
    """The pré-combat state (D77): attacker_picks/defender_picks (3 ranked
    civilizations each), sabotages (per side, 2 max), validated flags,
    ``sent`` (the T-5 DM left), ``resolved_pickban`` (civs revealed) and
    ``forfeit_side`` (the side that missed its validation)."""
    score: dict[str, Any] = Field(default_factory=dict)
    """The RDV score phase (attaques-score-v1): ``opened`` (the phase
    started at T), ``ready`` (the player ids that clicked Prêt),
    ``ready_deadline`` (epoch seconds — the T+5 no-show line),
    ``declarations`` (player id -> winner/game_id/at), ``game_id`` (the
    last declared game) and ``disputed`` (a contrary declaration came
    in after the resolution — admin arbitration needed)."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> AttackModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)

    @property
    def is_over(self) -> bool:
        """Whether the attack reached a final state."""
        return self.state in (AttackState.RESOLVED, AttackState.EXPIRED)


class SeasonState(BaseModel):
    """The current season: its schedule and progression (D1, D38, D52).

    ``current_cycle`` counts completed cycles; ``current_age_key`` is
    the age the season is in (indexes the config ages).
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    started_at: datetime
    weeks: int = Field(ge=1)
    current_cycle: int = Field(default=0, ge=0)
    current_age_key: str
    imposed_kingdoms: bool = False
    phase: str = "started"
    """Where the season stands (Drasah's game-design phases):

    ``setup`` — the season is launched, kingdoms form and players
    enroll, but NO random content exists (no civilization draft, no
    territory distribution — nothing is revealed before the game
    actually starts); ``started`` — the admin started the game: the
    civilization draft and the territory distribution happened;
    ``age`` / ``cycling`` / ``ended`` — future lots (epoch progression,
    weekly cycles, season end). Legacy documents without the field
    default to ``started`` so nothing changes for running seasons.
    """
    out_maps: list[str] = Field(default_factory=list)
    finished: bool = False
    frozen: bool = False
    """The game-clock PAUSE (Drasah's time machine, 2026-10-11): when
    True the weekly scheduler replays NO slot — the season stands
    still until an admin resumes it. Manual time-machine jumps still
    work while frozen (Drasah keeps testing while paused). Set
    automatically by every snapshot RESTORE: a rewind must not be
    instantly re-advanced by the real clock. Legacy documents without
    the field default to False (the game never pauses on its own).
    """
    winner_kingdom_id: str | None = None
    clock_offset: int = Field(default=0, ge=0)
    """Game-clock test offset in seconds (machine-temps-v2, Drasah
    14/10): the whole mod reads ``game_now() = real now + clock_offset``
    through the single seam — battle phases, locks, cooldowns and the
    cron slots all ride the game clock. Test tooling ONLY: a normal
    game always runs at 0. Never negative (a rewind goes through the
    time-machine snapshots, never through the clock). Snapshots carry
    the field: a restore replays the offset of its day verbatim."""
    lords_day_forced_start: datetime | None = None
    """Manual test override: when set (with ``lords_day_forced_end``),
    the Lord's Day window is FORCED open between the two timestamps —
    Drasah's test seam on the Paramètres panel (2026-10-11). Outside
    that range the real calendar window (Sunday 23:30 → Monday 10:00)
    applies alone."""
    lords_day_forced_end: datetime | None = None
    pending_marriage_losses: list[str] = Field(default_factory=list)
    """Kingdom ids that lost a combat since the last cycle switch: their
    lords' marriages drop at the next recalculation (D34/D53)."""

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> SeasonState:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class DuelModel(BaseModel):
    """One ShowMatch PA2 duel (D50): a 1v1 between two lords.

    Maps come from the duelist's own territories without reuse, civs
    never repeat, and the Megarandom fallback picks a random civ when
    the pool is empty. ``players`` holds the team members of each side
    once the format escalates to 2v2/3v3.
    """

    model_config = ConfigDict(strict=True)

    index: int
    format: str = "1v1"
    side_a: list[str] = Field(default_factory=list)
    side_b: list[str] = Field(default_factory=list)
    map_key: str | None = None
    civilizations: dict[str, str] = Field(default_factory=dict)
    winner_side: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> DuelModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class ShowMatchModel(BaseModel):
    """The ShowMatch PA2 deciding a tied Conquest finish (D50).

    Two kingdoms play mandatory duels until one leads 2-0 (1v1 first,
    then 2v2, 3v3... when every lord has dueled); a 1-1 tie chains a
    new duel with other lords. Maps and civs never repeat.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    kingdom_a: str
    kingdom_b: str
    score_a: int = Field(default=0, ge=0)
    score_b: int = Field(default=0, ge=0)
    duels: list[DuelModel] = Field(default_factory=list)
    used_map_keys: list[str] = Field(default_factory=list)
    used_civilizations: list[str] = Field(default_factory=list)
    finished: bool = False
    winner_kingdom_id: str | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> ShowMatchModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)


class FFAExplorationModel(BaseModel):
    """One Saturday FFA Exploration (ffa-exploration-v1, Drasah 14/10/2026).

    The bot draws the start hour at random among the configured full
    hours (default 10h-20h Paris), announces the FFA 2h before in the
    Géopolitique salon (the announcement opens the enrollments), and
    the players reserve a slot through the persistent « Aller
    explorer » button — first come, first served, up to the Gaïa
    free-for-all participant ceiling. One single 1h15 game mixes the
    AI Gaïa and every participant; the ranking comes back through the
    game contract (D20) and the rewards flow through
    ``EventService.run_exploration`` (map for the winner + graduated
    tech points). A registered participant absent from the ranking is
    a no-show: 72h combat lock starting at the next cycle switch.
    """

    model_config = ConfigDict(strict=True)

    id: str = Field(alias="_id")
    season_id: str
    date: str
    """The Saturday this FFA belongs to (ISO date, Paris timezone)."""
    start_at: datetime
    """The drawn start time (UTC, tz-aware)."""
    announce_at: datetime
    """Start minus the announce lead (2h by default) — when the
    Géopolitique announcement opens the enrollments."""
    one_lord_per_kingdom: bool = True
    """The admin parameter: ``1 seigneur par royaume`` (default) or
    ``libre`` — snapshotted from the config at the draw."""
    announced: bool = False
    started: bool = False
    finished: bool = False
    participants: list[str] = Field(default_factory=list)
    """The lord ids holding a slot (first come, first served)."""
    ranking: list[str] = Field(default_factory=list)
    """The lord ids best-first, as reported by the game contract."""
    no_shows: list[str] = Field(default_factory=list)
    """The registered participants absent from the game (72h lock)."""
    resolved_at: datetime | None = None

    def to_mongo(self) -> dict[str, Any]:
        """Convert to a MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict[str, Any]) -> FFAExplorationModel:
        """Build from a MongoDB document."""
        return _from_wire(cls, data)
