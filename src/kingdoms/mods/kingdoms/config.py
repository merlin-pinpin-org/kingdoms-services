"""Kingdoms mod season configuration.

Season configuration is **data**: every tunable of reference §27 lives in
``config/kingdoms/season.yaml`` (or the settings section of the mod
declaration) — never hardcoded. Defaults here are the Season II values
from the reference document; the admin overrides them without touching
code (decisions D9 — technology costs parametrizable — and D36).

The config is deliberately plain (Pydantic, strict): it is validated once
at load and shared by the domain services of later slices.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

SEASON_CONFIG_FILENAME = "season.yaml"


class NameRules(BaseModel):
    """Kingdom name rules — length and characters, admin-tunable (D21)."""

    model_config = ConfigDict(strict=True)

    min_length: int = Field(default=2, ge=1)
    max_length: int = Field(default=32, ge=1)
    pattern: str = r"^[\w\s'\-]+$"


class MapTypes(BaseModel):
    """The 12 Drasah cadastre tags (machine-temps… non — cadastre-diplomatie-v1).

    Drasah's official nomenclature (15/10/2026, source of truth:
    ``cadastre-maps-liste.md``): ``eau`` (true water victory), ``hybride``
    (winnable by land — NOT the Eau tag), ``objectif`` (a precise victory
    spot, often mid), ``nomade``, ``foret``, ``montagne``, ``desert``,
    ``marais``, ``ouverte`` (and its inverse ``ferme``), ``chaotique`` and
    ``mur_depart`` (start wall). These tags REPLACE the embryonic v0
    flags — NOT 1:1 (ex. Douves d'équipe = Hybride, Falaise = Chaotique,
    Lac Fantôme = Ouverte). They drive the Diplomatie civ conditions
    AND the Scriptorium/panel displays.
    """

    model_config = ConfigDict(strict=True)

    eau: bool = False
    hybride: bool = False
    objectif: bool = False
    nomade: bool = False
    foret: bool = False
    montagne: bool = False
    desert: bool = False
    marais: bool = False
    ouverte: bool = False
    chaotique: bool = False
    mur_depart: bool = False
    ferme: bool = False

    def has(self, tag: str) -> bool:
        """Whether one Drasah tag is set (unknown tags read as False)."""
        return bool(getattr(self, tag, False))


class MapEntry(BaseModel):
    """One allowed map of the admin-managed catalog (§6, issue #163).

    ``cadastre`` carries the map's cadastre effects as raw data
    (CADASTRE.md) — keyed effect descriptors the later slices apply;
    this slice keeps them admin-editable without touching code.
    """

    model_config = ConfigDict(strict=True)

    key: str
    display_name: str
    types: MapTypes = Field(default_factory=MapTypes)
    cadastre: dict[str, Any] = Field(default_factory=dict)


class Epoch(BaseModel):
    """One age of the season (reference §15, decision D18)."""

    model_config = ConfigDict(strict=True)

    key: str
    display_name: str
    gaia_ai_level: int = Field(ge=1, le=5)
    tech_points: int = Field(ge=0)
    extra_marriages: int = Field(ge=0)


class TechnologyCosts(BaseModel):
    """Technology costs and purchase limits.

    Costs (tech points) and per-season purchase limits of the ten
    special actions (reference §20, decisions D9/D36).

    ``limit`` is the maximum number of purchases per season (0 = no
    limit beyond affordability).
    """

    model_config = ConfigDict(strict=True)

    embuscade: int = Field(default=2, ge=0)
    traquenard: int = Field(default=1, ge=0)
    patrouille: int = Field(default=2, ge=0)
    contre_espionnage: int = Field(default=2, ge=0)
    sabotage: int = Field(default=1, ge=0)
    explorateur: int = Field(default=2, ge=0)
    jeu_d_armes: int = Field(default=1, ge=0)
    mariage_arrange: int = Field(default=3, ge=0)
    corruption: int = Field(default=4, ge=0)
    garde_royale: int = Field(default=2, ge=0)

    limits: dict[str, int] = Field(
        default_factory=lambda: {
            "jeu_d_armes": 1,
            "patrouille": 2,
            "sabotage": 2,
            "explorateur": 1,
            "corruption": 2,
        }
    )
    """Per-season purchase limits (D36/D58); 0 or absent = unlimited.

    Corruption is capped at 2 purchases per season (D58). Sabotage is
    additionally capped per game (2 sniped civilizations, D12) by the
    attack service, not by this counter."""


class AttackSettings(BaseModel):
    """Attack/defense budgets and delays (reference §11-§13)."""

    model_config = ConfigDict(strict=True)

    attacks_per_week: int = Field(default=1, ge=0)
    defenses_per_week: int = Field(default=1, ge=0)
    player_attack_delay_minutes: int = Field(default=360, ge=0)
    """Declaration → game delay in MINUTES (beta-weekend-v2, Drasah
    17/10/2026): the Season II default stays 6h (360) — the beta
    preset compresses it to 10 minutes without touching the code."""
    gaia_attack_delay_minutes: int = Field(default=180, ge=0)
    """Gaïa free-for-all delay in MINUTES (Season II default 3h)."""
    gaia_attack_max_per_kingdom: int = Field(default=1, ge=1)
    gaia_attack_max_total: int = Field(default=7, ge=1)
    gaia_max_participants: int = Field(default=7, ge=1)
    defense_cutoff_minutes: int = Field(default=15, ge=0)
    """Phases d'attaque (D77): the defense closes at T-15 — after that
    the defender can no longer react, the cover is revealed and the
    kingdom's bot AI takes the defense when nobody answered."""
    pick_ban_lead_minutes: int = Field(default=5, ge=0)
    """Phases d'attaque (D77): the pick & ban DMs leave at T-5; each
    side ranks 3 civilizations and can engage up to 2 sabotages."""
    embuscade_delay_minutes: int = Field(default=60, ge=1)
    """Phases d'attaque (D77): the Embuscade declares at T-1h sharp
    (defense window = 45 minutes through the T-15 cutoff). In MINUTES
    since beta-weekend-v2 (the default keeps the exact 1h)."""
    no_defense_outcome: str = Field(default="vs_ai")
    """D8: what happens when no defender answers in the delay.

    ``vs_ai`` (default, D77): the defending kingdom's bot AI takes the
    defense at T-15 and the game result still comes through the game
    contract (D20) — auto_victory is legacy (the attacker captured the
    territory without a game) and no longer the default."""
    ready_grace_minutes: int = Field(default=5, ge=0)
    """The T+grace no-show line of a meeting (minutes)."""
    ai_draft_grace_minutes: int = Field(default=15, ge=0)
    """battle-ai-rdv-v1 (Drasah 22/10): vs a bot-AI defense the draft
    runs AT the meeting time (the AI never bans, it picks 3 random
    civilizations and validates after a 30-90 s random delay) — this
    is the window the ATTACKER gets to answer his own draft DM before
    the anti-blocking default (random civs, never a forfeit) applies.
    15 minutes: the exact window the human draft enjoys (T-15 → T)."""
    planning_max_hours: int = Field(default=24, ge=1)
    """planification-attaque-v1 (Drasah 23/10/2026): the golden 24h
    rule — a planned attack's RDV (T) never sits further than this
    horizon, and T is never closer than the declaration + itinéraire
    (the scheduled declaration fires at T - travel delay)."""
    room_cleanup_delay_minutes: int = Field(default=30, ge=0)
    """Drasah 18/10 v4: the battle room survives this long (minutes)
    after the meeting resolved — the room only disappears once a
    winner is known, then lingers for a post-game wrap-up."""

    """Score phase (beta-weekend-v1, Drasah 17/10/2026): the no-show
    grace past the meeting time (T+5 by default) — the phase where
    both sides click Prêt, a missing Prêt at the deadline being a
    forfeit. Configurable so a beta weekend can compress it."""
    score_reminder_hours: float = Field(default=3.0, ge=0)
    """Score phase (beta-weekend-v1): hours after the game start
    before the result reminder DM leaves (3h by default)."""
    score_dispute_after_reminder_hours: float = Field(default=12.0, ge=0)
    """Score phase (beta-weekend-v1, bug n°2 / Check 61): hours after
    the reminder before a result-less meeting auto-opens its dispute
    (12h by default — the admins arbitrate via the console)."""
    score_confirm_minutes: int = Field(default=10, ge=0)
    """Score phase (Drasah 18/10 v2): minutes a LONE result
    declaration stays pending — the other player may contest it
    (dispute) inside the window, after which the declared result is
    confirmed. The battle room declares the results now."""


class VoliereSettings(BaseModel):
    """Volière cooldowns in hours (voliere-v1, Drasah 14/10/2026).

    The cooldowns are PER KINGDOM and shared by every lord of the
    kingdom; the development itself is free from the Âge Féodal.
    """

    model_config = ConfigDict(strict=True)

    corbeau_civ_cooldown_hours: int = Field(default=72, ge=0)
    corbeau_verify_cooldown_hours: int = Field(default=24, ge=0)
    corbeau_track_cooldown_hours: int = Field(default=48, ge=0)
    corbeau_track_window_hours: int = Field(default=24, ge=1)
    corbeau_theft_delay_hours: int = Field(default=1, ge=0)
    """Corbeau vol de ⚙️ (corbeau-v3): delay between the send and the
    execution (the Corbeau flies 1h before the first theft lands)."""
    corbeau_theft_contest_minutes: int = Field(default=5, ge=0)
    """Corbeau vol de ⚙️ (corbeau-v3): the 5-minute window the robbed
    kingdom has to CONTEST after the alarm rings (first ⚙️ already
    stolen — the contest kills the Corbeau and refunds EVERYTHING)."""
    faucon_territory_cooldown_hours: int = Field(default=96, ge=0)
    faucon_sabotage_cooldown_hours: int = Field(default=48, ge=0)
    faucon_release_cooldown_hours: int = Field(default=168, ge=0)
    faucon_release_window_hours: int = Field(default=24, ge=1)
    pigeon_civ_cooldown_hours: int = Field(default=72, ge=0)
    pigeon_question_cooldown_hours: int = Field(default=1, ge=0)
    pigeon_dm_cooldown_hours: int = Field(default=1, ge=0)


class EventSettings(BaseModel):
    """Weekly event schedules (reference §16-§17, decision D1).

    Cron expressions keep the admin free to move every event without a
    code change; the defaults are the Season II values.
    """

    model_config = ConfigDict(strict=True)

    cycle_cron: str = "30 23 * * SUN"
    age_cron: str = "0 0 * * WED"
    exploration_cron: str = "0 14 * * SAT"
    lords_day_new_maps: int = Field(default=8, ge=0)
    # Drasah's Lord's Day window (2026-10-11): the weekly switch on
    # Sunday 23:30 opens a protected window until the NEXT day at
    # ``lords_day_end_hour`` (Monday 10:00) — an attack cannot be
    # PLAYED inside the window, but declaring one is allowed when the
    # execution lands after the window closes.
    lords_day_end_hour: int = Field(default=10, ge=0, le=23)
    # The weekly crons are interpreted in this timezone (the players'
    # wall clock), not in UTC: Sunday 23:30 means 23:30 in Paris.
    schedule_timezone: str = "Europe/Paris"
    exploration_first_tech: int = Field(default=1, ge=0)
    exploration_second_tech: int = Field(default=3, ge=0)
    exploration_third_tech: int = Field(default=2, ge=0)
    exploration_other_tech: int = Field(default=1, ge=0)
    # ------------------------------------------------------------------
    # FFA Exploration (ffa-exploration-v1, Drasah 14/10/2026)
    # ------------------------------------------------------------------
    ffa_enabled: bool = True
    """Whether the Saturday FFA Exploration runs at all."""
    ffa_one_lord_per_kingdom: bool = True
    """The admin parameter: ``1 seigneur par royaume`` (default) or
    ``libre`` — snapshotted on each FFA at the draw, so an admin can
    flip the default between two Saturdays."""
    ffa_start_hours: tuple[int, ...] = (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20)
    """The full Paris hours the Saturday start is drawn among."""
    ffa_announce_lead_hours: int = Field(default=2, ge=0)
    """The announcement lands this many hours before the start, and
    the enrollments open with it (Drasah's revision: no more midnight
    announcement — 2h before the start)."""
    ffa_game_minutes: int = Field(default=75, ge=1)
    """The single FFA game duration (1h15)."""
    ffa_no_show_lock_hours: int = Field(default=72, ge=0)
    """A registered participant absent from the game is locked out of
    combat for this many hours starting AT THE NEXT CYCLE SWITCH."""


class CivilizationCondition(BaseModel):
    """One civilization unlock condition (Diplomatie V1, Drasah 15/10/2026).

    Pure data — the engine (``DiplomacyService._condition_met``) reads
    ONLY these fields. V1 semantics:

    - ``requires_map_keys`` + ``min_map_key_count`` — owning at least
      ``min_map_key_count`` of the listed maps unlocks (default 1: ANY
      of them — the Japonais Archipel OU Ile volcanique already rides
      this; Goths/Hindoustanis count several);
    - ``requires_map_types`` + ``min_map_type_count`` — owning at least
      ``min_map_type_count`` territories carrying one of the Drasah
      tags (default 1, the binary v0 behaviour);
    - ``min_map_count`` / ``max_map_count`` — the total owned-territory
      thresholds (Chinois « moins de 3 » → max 2, « plus de N » →
      N+1: the comparison stays inclusive in the data);
    - ``requires_civilization`` — the unlock chain (Shu/Wei/Wu behind
      Chinois);
    - ``requires_kingdom_name_pattern`` — the referential Hungarian rule
      (Royaume des HeN);
    - ``requires_tech_points`` — the kingdom's banked tech points
      (Birmans);
    - ``requires_marriages`` — the kingdom's married-lord count
      (Bohémiens « marié deux fois »);
    - ``requires_civilization_count`` — the number of civilizations the
      kingdom already holds (Portugais « plus de 4 » → 5).
    """

    model_config = ConfigDict(strict=True)

    key: str
    display_name: str
    requires_map_keys: tuple[str, ...] = ()
    min_map_key_count: int = Field(default=1, ge=1)
    requires_map_types: tuple[str, ...] = ()
    min_map_type_count: int = Field(default=1, ge=1)
    min_map_count: int | None = Field(default=None, ge=0)
    max_map_count: int | None = Field(default=None, ge=0)
    requires_civilization: str | None = None
    requires_kingdom_name_pattern: str | None = None
    requires_tech_points: int | None = Field(default=None, ge=0)
    requires_marriages: int | None = Field(default=None, ge=0)
    requires_civilization_count: int | None = Field(default=None, ge=0)


class ShowMatchSettings(BaseModel):
    """ShowMatch PA2 tunables (D50)."""

    model_config = ConfigDict(strict=True)

    fallback_map_key: str = "megarandom"
    wins_needed: int = Field(default=2, ge=1)


class ParishTier(BaseModel):
    """One Paroisse palier (D64, Drasah's 2026-10-11 values).

    ``cost`` is the tech-point price of the upgrade TO this palier (0
    for the starting Chapelle), ``marriage_lock_hours`` the combat lock
    of a fresh standard marriage at this palier, ``offering`` the
    Offrande — tech points credited to the treasury at every marriage
    celebrated at this palier or above.
    """

    model_config = ConfigDict(strict=True)

    key: str
    display_name: str
    cost: int = Field(default=0, ge=0)
    marriage_lock_hours: int = Field(default=24, ge=0)
    offering: int = Field(default=0, ge=0)


class ParishSettings(BaseModel):
    """The three-palier Paroisse ladder + its refill knobs (D64/D59)."""

    model_config = ConfigDict(strict=True)

    tiers: tuple[ParishTier, ...] = (
        ParishTier(key="chapelle", display_name="Chapelle", cost=0, marriage_lock_hours=24, offering=0),
        ParishTier(key="eglise", display_name="Église", cost=2, marriage_lock_hours=12, offering=1),
        ParishTier(key="cathedrale", display_name="Cathédrale", cost=4, marriage_lock_hours=6, offering=2),
    )
    lords_day_points: int = Field(default=1, ge=0)
    """Marriage points granted at every Lord's Day (Chapelle bonus, D64)."""

    cathedral_cost: int = Field(default=3, ge=0)
    """Lieu Sacrée consecration price in tech points (lieu-sacree-v1)."""

    cathedral_chantier_hours: int = Field(default=72, ge=1)
    """Chantier duration: survives it and the territory becomes SACRED."""

    cathedral_destroy_reward: int = Field(default=2, ge=0)
    """Tech points banked by an attacker who destroys a chantier."""

    def tier(self, level: int) -> ParishTier:
        """Return the palier entry of a level (1-3, clamped)."""
        index = min(max(int(level), 1), len(self.tiers)) - 1
        return self.tiers[index]


class TavernDrink(BaseModel):
    """One Taverne drink (taverne-v1, Drasah 24/10/2026).

    A FIXED price in écus and a FIXED combat lock per drink; the
    effect rides the key (tranche 1 wires the pour only — the rumor
    window, the FFA schedule and the forced map land with their
    tranches): ``verre`` = the rumor window, ``vin`` = the next FFA
    Exploration hour, ``alcool`` = forcing one unrevealed map.
    """

    model_config = ConfigDict(strict=True)

    key: str
    label: str
    price: int = Field(default=1, ge=0)
    lock_minutes: int = Field(default=60, ge=0)


class TavernSettings(BaseModel):
    """The Taverne knobs (taverne-v1, Drasah 24/10/2026).

    Drasah's frozen values: every lord starts with 10 écus, the drinks
    cost 2/4/8 écus with a 1h/3h/6h combat lock, and a GLOBAL 24h
    cooldown separates two drinks (anti-spam).
    """

    model_config = ConfigDict(strict=True)

    purse_start: int = Field(default=10, ge=0)
    """The opening purse of every lord (écus)."""
    drink_cooldown_hours: int = Field(default=24, ge=0)
    """The GLOBAL delay between two drinks, whatever the drink."""
    drinks: tuple[TavernDrink, ...] = (
        TavernDrink(key="verre", label="🍺 Verre", price=2, lock_minutes=60),
        TavernDrink(key="vin", label="🍷 Vin", price=4, lock_minutes=180),
        TavernDrink(key="alcool", label="🥃 Alcool fort", price=8, lock_minutes=360),
    )
    """The three drinks — the panel and the service read ONLY this
    catalog (data, never code)."""

    def drink(self, key: str) -> TavernDrink | None:
        """Return the drink entry of a key (None when unknown)."""
        return next((drink for drink in self.drinks if drink.key == key), None)


class MercenarySettings(BaseModel):
    """The Chef Mercenaire knobs (chef-mercenaire-v1, Drasah 24/10/2026).

    Drasah's frozen values: the Chief stands in the Taverne between
    20h00 and 21h00 REAL Paris time (never the offset game clock),
    the bids open at 20h15 and the base close sits at 20h30 — an
    anti-snipe bid inside the last two minutes pushes the close one
    minute further, the HARD close is 20h45 and the results stay
    displayed until the Chief leaves at 21h00. Five civilizations are
    drawn from the catalogue minus the MARRIED ones (duplicates with
    the kingdoms' hands are allowed), each carrying a random, VISIBLE
    number of draft uses (1-3), starting bid 1 écu, and a marriage
    acquiring a held civ strips the mercenary holder of it and
    refunds him a FIXED 1 écu.
    """

    model_config = ConfigDict(strict=True)

    enabled: bool = True
    window_start_hour: int = Field(default=20, ge=0, le=23)
    """The Chief arrives at this REAL Paris hour (classic seasons — a
    beta console button calls him at any moment instead)."""
    window_end_hour: int = Field(default=21, ge=1, le=23)
    bids_open_minutes: int = Field(default=15, ge=0)
    base_close_minutes: int = Field(default=30, ge=0)
    hard_close_minutes: int = Field(default=45, ge=0)
    pool_size: int = Field(default=5, ge=1)
    min_uses: int = Field(default=1, ge=1)
    max_uses: int = Field(default=3, ge=1)
    starting_bid: int = Field(default=1, ge=1)
    antisnipe_trigger_minutes: int = Field(default=2, ge=1)
    antisnipe_extension_minutes: int = Field(default=1, ge=1)
    marriage_refund_ecus: int = Field(default=1, ge=0)


class KingdomsSeasonConfig(BaseModel):
    """Full season configuration.

    Reference §27: configuration data, persisted across seasons and
    modifiable by the admin.
    """

    model_config = ConfigDict(strict=True)

    weeks: int = Field(default=4, ge=1)
    kingdoms_count: int = Field(default=2, ge=1)
    lords_per_kingdom: int = Field(default=4, ge=1)
    territories_per_kingdom: int = Field(default=5, ge=0)
    gaia_territories: int = Field(default=8, ge=0)
    starting_civilizations: int = Field(default=0, ge=0)
    garrison_enabled: bool = False
    maps: tuple[MapEntry, ...] = ()
    ages: tuple[Epoch, ...] = ()
    civilizations: tuple[CivilizationCondition, ...] = ()
    hungarian_kingdom_pattern: str = "(?i).*hen.*"
    showmatch: ShowMatchSettings = Field(default_factory=ShowMatchSettings)
    names: NameRules = Field(default_factory=NameRules)
    technologies: TechnologyCosts = Field(default_factory=TechnologyCosts)
    parish: ParishSettings = Field(default_factory=ParishSettings)
    attacks: AttackSettings = Field(default_factory=AttackSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    voliere: VoliereSettings = Field(default_factory=VoliereSettings)
    tavern: TavernSettings = Field(default_factory=TavernSettings)
    mercenary: MercenarySettings = Field(default_factory=MercenarySettings)


def default_map_catalog() -> tuple[MapEntry, ...]:
    """Return Drasah's official 97-map cadastre (cadastre-diplomatie-v1).

    RULE (Drasah 15/10/2026, frozen): this list ERASES the v0 catalog —
    any map of the old code absent from this list DOES NOT EXIST (the
    v0 Altai, Babel, Hideout, Highland and Siberia are gone, and the
    ex-English labels become Drasah's French ones, verbatim). Every
    entry carries its cadastre bonus as display text (the official
    acquisition condition lives in the Diplomatie — a bonus text may
    diverge, ex. 3 maps display the Vikings while Iles du Nord alone
    unlocks them) and its Drasah tags. GARNISON bonuses are IGNORED in
    V1 (priority 3, V2) — Clairière fortifiée, Douves d'équipe,
    Isthme keep no bonus at all; Embranchement, Le passage keep only
    their first one. Plaine and Prairie carry no tag in Drasah's list.
    """
    rows: tuple[tuple[str, str, str, str | None], ...] = (
        # (key, display_name, Drasah tags, cadastre bonus — verbatim)
        ("acropolis", "Acropolis", "montagne,ouverte", "Offre un contre-espionnage/semaine"),
        ("arabia", "Arabie", "ouverte,desert", None),
        ("archipel", "Archipel", "hybride,eau", "Offre la civilisation des Japonais"),
        ("arena", "Arène", "mur_depart", "Offre une technologie « Jeu d'armes »"),
        ("atacama", "Atacama", "desert,objectif", None),
        ("baltique", "Baltique", "eau", "Réduit d'une heure le délai de l'une de vos agressions classiques"),
        ("banc-de-sable", "Banc de Sable", "eau", None),
        ("bancs", "Bancs", "hybride", None),
        ("bois-creux", "Bois Creux", "foret", "Offre un sabotage/semaine"),
        ("budapest", "Budapest", "eau", "Offre un contre-espionnage/semaine"),
        ("carrefour", "Carrefour", "hybride", None),
        ("cenotes", "Cénotes", "hybride", None),
        ("chaine-de-montagne", "Chaîne de Montagne", "montagne", None),
        ("clairiere", "Clairière", "foret", None),
        ("african-clearing", "Clairière Africaine", "nomade", "Offre la civilisation des Maliens"),
        ("clairiere-fortifiee", "Clairière fortifiée", "mur_depart", None),
        ("colline-aux-loups", "Colline aux loups", "montagne,objectif", "Offre un sabotage/Dimanche"),
        ("conquete-de-la-montagne", "Conquête de la Montagne", "montagne", None),
        ("continental", "Continent", "hybride", None),
        ("cote", "Côte", "hybride", "Offre la civilisation des Bengalis"),
        ("cratere", "Cratère", "montagne,objectif", None),
        ("crete-montagneuse", "Crète Montagneuse", "montagne", None),
        ("delta-du-nil", "Delta du Nil", "nomade,eau", "Offre la civilisation des Sarrasins"),
        ("douves-d-equipe", "Douves d'équipe", "hybride", None),
        ("dune-montagneuse", "Dune Montagneuse", "montagne,desert", None),
        ("embranchement", "Embranchement", "hybride", "Offre un sabotage/semaine"),
        ("migration", "Emigration", "eau,objectif", "Offre une exploration/semaine"),
        ("eruption", "Eruption", "montagne,objectif", None),
        ("faille-sableuse", "Faille sableuse", "desert,ouverte", None),
        ("falaise", "Falaise", "chaotique", "Offre un sabotage/Dimanche"),
        ("flot-dore", "Flot doré", "objectif,hybride", "Offre un point technologie/Dimanche"),
        ("foret-cotiere", "Forêt côtière", "foret,hybride", "Offre une Embuscade/Dimanche"),
        ("black-forest", "Forêt noire", "foret", "Offre la civilisation des Celtes"),
        ("fortress", "Forteresse", "mur_depart", "Offre une technologie « Jeu d'armes »"),
        ("forteresse-circulaire", "Forteresse Circulaire", "mur_depart", None),
        ("gorges", "Gorges", "montagne", "Offre la civilisation des Géorgiens"),
        ("gouffre-dore", "Gouffre doré", "objectif", "Offre un point de technologie/Dimanche"),
        ("groenland", "Groenland", "hybride", "Offre la civilisation des Vikings"),
        ("haboob", "Haboob", "desert,chaotique", None),
        ("hamburger", "Hamburger", "hybride", "Offre la civilisation des Bourguignons"),
        ("huis-clos", "Huis Clos", "ouverte", None),
        ("ile-volcanique", "Ile volcanique", "hybride,montagne", "Offre la civilisation des Japonais"),
        ("islands", "Iles", "eau", "Offre la civilisation des Britanniques"),
        ("iles-du-nord", "Iles du Nord", "eau", "Offre la civilisation des Vikings"),
        ("ile-du-pacifique", "Ile du Pacifique", "eau", "Offre la civilisation des Malais"),
        ("ile-marecageuse", "Ile marécageuse", "eau,marais", "Offre une « Embuscade »/Dimanche"),
        ("ile-partenaire", "Ile partenaire", "eau", "Offre un mariage/semaine"),
        ("isthme", "Isthme", "hybride", None),
        ("jungle-de-la-mangrove", "Jungle de la mangrove", "marais,foret,hybride", "Offre la civilisation "
         "des Aztèques"),
        ("karsts", "Karsts", "ouverte", None),
        ("kawasan", "Kawasan", "hybride", None),
        ("kilimandjaro", "Kilimandjaro", "montagne", None),
        ("la-cite-des-lacs", "La cité des lacs", "hybride", None),
        ("crater-lake", "Lac du cratère", "eau", None),
        ("ghost-lake", "Lac Fantôme", "ouverte", "Offre les civilisations des Polonais & des Slaves"),
        ("lac-glacial", "Lac Glacial", "hybride", None),
        ("lac-alpin", "Lac Alpins", "ouverte", None),
        ("le-fort-de-la-colline", "Le fort de la colline", "mur_depart,montagne", "Augmente votre capacité "
         "de patrouille de 1"),
        ("le-passage", "Le passage", "eau", "Offre une exploration/dimanche"),
        ("lombardie", "Lombardie", "ouverte", "Offre la civilisation des Italiens & des Siciliens"),
        ("marais", "Marais", "marais,ouverte", None),
        ("marais-d-or", "Marais d'or", "marais,objectif,hybride", "Offre un point de technologie/dimanche"),
        ("salt-marsh", "Marais salants", "ouverte,marais", None),
        ("marche", "Marché", "ouverte", "Offre la civilisation des Perses"),
        ("mediterranee", "Méditerranée", "hybride", "Réduit le délai minimum de vos agressions classiques d'une heure"),
        ("megarandom", "Megarandom", "chaotique", None),
        ("michi", "Michi", "foret,ferme", None),
        ("mongolia", "Mongolie", "ouverte", "Offre la civilisation des Mongols"),
        ("montagnes", "Montagnes", "montagne,hybride", None),
        ("nomad", "Nomade", "nomade,hybride", None),
        ("nomade-marin", "Nomade Marin", "nomade,eau", None),
        ("nomade-terrestre", "Nomade terrestre", "nomade", None),
        ("oasis", "Oasis", "desert,ferme", None),
        ("passage-montagneux", "Passage montagneux", "nomade,montagne", "Augmente le délai d'attaque minimum "
         "de vos adversaires de 1h"),
        ("pataugeoire", "Pataugeoire", "marais,objectif,ouverte", None),
        ("pierre-runiques", "Pierre runiques", "ouverte", "Offre un mariage/dimanche"),
        ("plaine", "Plaine", "", None),
        ("prairie", "Prairie", "", None),
        ("quatre-lacs", "Quatre lacs", "hybride", None),
        ("refuge", "Refuge", "mur_depart", "Offre la map Emigration OU 2 points de technologie"),
        ("repercussions", "Répercussions", "ouverte,objectif", "Offre la civilisation des Huns"),
        ("rivieres", "Rivières", "hybride", None),
        ("gold-rush", "Ruée vers l'or", "ouverte,objectif", "Offre un point technologie/Dimanche"),
        ("scandinavie", "Scandinavie", "hybride", "Offre la civilisation des Vikings"),
        ("serengeti", "Serengeti", "ouverte", "Offre la civilisation des Éthiopiens"),
        ("socotra", "Socotra", "chaotique", None),
        ("sources-des-dunes", "Sources des dunes", "desert,hybride", None),
        ("sources-sacrees", "Sources sacrées", "ouverte", "Offre un mariage arrangé/dimanche · "
         "Offre la civilisation des Khmers"),
        ("steppe", "Steppe", "nomade", None),
        ("terrain-escarpe", "Terrain escarpé", "montagne,ouverte", "Offre une Embuscade/semaine"),
        ("terre-de-la-folie", "Terre de la folie", "ouverte", None),
        ("terre-verdoyante", "Terre verdoyante", "ouverte", None),
        ("tourbiere", "Tourbière", "marais,nomade", "Offre la civilisation des Vietnamiens"),
        ("amazon-tunnel", "Tunnel Amazonien", "foret,ferme", "Offre la civilisation des Incas"),
        ("vallee", "Vallée", "ouverte", None),
        ("yucatan", "Yucatan", "ferme", "Offre la civilisation des Mayas"),
    )
    return tuple(
        MapEntry(
            key=key,
            display_name=name,
            types=MapTypes(**{tag: True for tag in tags.split(",") if tag}),
            cadastre={"bonus": bonus} if bonus else {},
        )
        for key, name, tags, bonus in rows
    )


def default_season_config() -> KingdomsSeasonConfig:
    """Return the Season II default configuration (reference §15, D18)."""
    return KingdomsSeasonConfig(
        maps=default_map_catalog(),
        ages=(
            Epoch(key="dark_age", display_name="Âge sombre", gaia_ai_level=2, tech_points=0, extra_marriages=1),
            Epoch(key="feudal_age", display_name="Âge féodal", gaia_ai_level=3, tech_points=1, extra_marriages=1),
            Epoch(key="castle_age", display_name="Âge des châteaux", gaia_ai_level=5, tech_points=2, extra_marriages=1),
            Epoch(key="imperial_age", display_name="Âge impérial", gaia_ai_level=5, tech_points=2, extra_marriages=1),
        ),
    )


def _tuples(value: Any) -> Any:
    """Recursively turn the YAML lists into tuples (strict pydantic)."""
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    if isinstance(value, dict):
        return {key: _tuples(item) for key, item in value.items()}
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge a YAML override onto the defaults.

    Dicts merge, the rest — tuples included — replaces. A partial
    override must never wipe the defaults it does not mention
    (maps, ages...).
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_season_config(config_dir: Path) -> KingdomsSeasonConfig:
    """Load the season configuration from ``<config_dir>/kingdoms/season.yaml``.

    The file is a partial override deep-merged onto the Season II
    defaults: an admin may tune the civilizations without re-listing
    the maps, and the game stays playable with no file at all. An
    invalid override raises: a broken config must fail loudly, never
    load half-validated.
    """
    season_file = config_dir / "kingdoms" / SEASON_CONFIG_FILENAME
    if not season_file.is_file():
        return default_season_config()
    with open(season_file, encoding="utf-8") as fh:
        data: Any = yaml.safe_load(fh)
    if data is None:
        return default_season_config()
    if not isinstance(data, dict):
        raise ValueError(f"{season_file}: season config must be a mapping")
    merged = _merge(default_season_config().model_dump(), _tuples(data))
    return KingdomsSeasonConfig.model_validate(merged)
