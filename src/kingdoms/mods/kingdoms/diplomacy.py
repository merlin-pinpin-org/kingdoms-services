"""Kingdoms mod diplomacy & marriages service (kingdoms-services#160, T6).

Reference §18-§19 and decisions D32-D34, D45, D51, D53, D59-D60,
D74: the civilization-conditions engine (CIVILIZATIONS.md as admin
data), the per-kingdom playable-civilization list recomputed at every
cycle end, and the marriages (standard and arranged) that secure a
civilization.

Alliances equal civilizations (D51): the recomputed list is what the
diplomacy surface displays; this service owns the rules only.
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from kingdoms.mods.kingdoms.game_clock import game_now
from kingdoms.mods.kingdoms.service import (
    KingdomNotFoundError,
    KingdomsModError,
    NoSeasonError,
)

if TYPE_CHECKING:
    from kingdoms.mods.kingdoms.config import (
        CivilizationCondition,
        KingdomsSeasonConfig,
    )
    from kingdoms.mods.kingdoms.models import KingdomModel, LordModel, SeasonState
    from kingdoms.mods.kingdoms.service import KingdomsService
    from kingdoms.mods.kingdoms.storage import KingdomsStore
    from kingdoms.mods.kingdoms.territories import TerritoryService

logger = logging.getLogger("kingdoms.diplomacy")


_PROMISES: dict[str, tuple[str, ...]] = {
    "arméniens": (
        "Zabel la Juste",
        "Parandzem",
        "Ashkhen",
        "Khanduts",
        "Shushanik",
    ),
    "aztèques": (
        "Atotoztli",
        "Papantzin",
        "Azcalxochitzin",
        "Tlacuilolxochitzin",
        "Teotlalco",
    ),
    "bengalis": (
        "Maynamati",
        "Lakshmi Sen",
        "Ramadevi Choudhury",
    ),
    "berbères": (
        "Dihya « la Kahina »",
        "Tin Hinan",
        "Zaynab al-Nafzawiya",
        "Lalla Fatma N'Soumer",
    ),
    "birmans": (
        "Shin Sawbu",
        "Saw Min Hla",
        "Atula Sanda Dewi",
    ),
    "bohémiens": (
        "Ludmila de Bohême",
        "Anežka Přemyslovna",
        "Kunhuta Habsburská",
        "Žofie Bavorská",
        "Libuše",
    ),
    "bourguignons": (
        "Marguerite de Bourgogne",
        "Bonne de Berry",
        "Isabeau de Bavière",
        "Marie de Bourgogne",
    ),
    "britanniques": (
        "Æthelflæd",
        "Mathilde l'Emperesse",
        "Aliénor d'Aquitaine",
        "Emma de Normandie",
        "Boudicca",
    ),
    "bulgares": (
        "Kera Tamara",
        "Maria Paléologue",
        "Théodora",
        "Anna de Bulgarie",
    ),
    "byzantins": (
        "Théodora",
        "Zoé Porphyrogénète",
        "Irène l'Athénienne",
        "Anne Comnène",
        "Eudokia Makrembolitissa",
    ),
    "celtes": (
        "Boudicca",
        "Cartimandua",
        "Medb",
        "Macha Mong Ruad",
    ),
    "chinois": (
        "Wu Zetian",
        "Lü Zhi",
        "Wang Zhengjun",
        "Yang Guifei",
        "Xiaozhuang",
    ),
    "coréens": (
        "Sondok",
        "Jindeok",
        "Jinseong",
        "Heo Hwang-ok",
        "Munjeong",
    ),
    "coumans": (
        "Elizabeth la Coumane",
        "Anna de Bulgarie",
        "Elena",
    ),
    "danois": (
        "Thyra Danebod",
        "Gunnhild Konungamóðir",
        "Margrete Valdemarsdatter",
        "Estrid Svendsdatter",
        "Sigrid la Haute",
    ),
    "dravidiens": (
        "Sembiyan Mahadevi",
        "Kundavai",
        "Lokamahadevi",
        "Viramahadevi",
        "Machchidevi",
    ),
    "espagnols": (
        "Isabelle Iʳᵉ de Castille",
        "Urraca Iʳᵉ de León",
        "Jeanne de Castille",
        "Blanche de Navarre",
        "Pétronille d'Aragon",
    ),
    "éthiopiens": (
        "Gudit",
        "Eleni",
        "Mentewab",
        "Taytu Betul",
        "Makeda",
    ),
    "francs": (
        "Brunehaut",
        "Bathilde",
        "Clothilde",
        "Rigunthe",
        "Berthe aux Grands Pieds",
    ),
    "géorgiens": (
        "Tamar la Grande",
        "Rusudan",
        "Ketevan de Kakheti",
        "Gulkan",
        "Dinar",
    ),
    "goths": (
        "Amalaswinthe",
        "Theodelinde",
        "Matasunthe",
        "Gaïswinthe",
        "Rosamunde",
    ),
    "gurjaras": (
        "Kurmadevi",
        "Naikadevi",
        "Mira Bai",
        "Padmini",
        "Rukmini",
    ),
    "hindoustanis": (
        "Razia Sultana",
        "Nur Jahan",
        "Jodha Bai",
        "Mumtaz Mahal",
        "Roopmati",
    ),
    "huns": (
        "Kreka",
        "Ildico",
        "Fille d'Eskam",
        "Justa Grata Honoria",
    ),
    "incas": (
        "Anahuarque",
        "Cusirimay",
        "Quispe Sisa",
        "Tocto Ocllo",
        "Mama Ocllo",
    ),
    "italiens": (
        "Matilde de Canossa",
        "Constance de Hauteville",
        "Bianca Lancia",
        "Béatrice de Provence",
        "Adélaïde de Susa",
    ),
    "japonais": (
        "Himiko",
        "Suiko",
        "Kōken",
        "Hōjō Masako",
        "Saigō no Tsubone",
    ),
    "jürchen": (
        "Impératrice Mingde",
        "Impératrice Daoping",
        "Impératrice Tailing",
        "Impératrice Cheng'an",
    ),
    "khitan": (
        "Xiao Yanyan",
        "Yelü Pusuwan",
        "Impératrice Shunsheng",
        "Impératrice Ruizhi",
        "Impératrice Huizhi",
    ),
    "khmers": (
        "Jayarajadevi",
        "Indradevi II",
        "Indradevi I",
        "Nrpatindradevi",
    ),
    "lituaniens": (
        "Birutė",
        "Danutė",
        "Ona Vytautienė",
        "Aldona",
        "Rimgailė",
    ),
    "magyars": (
        "Sarolt",
        "Gisèle de Bavière",
        "Margit",
        "Piroska",
        "Ilona",
    ),
    "malais": (
        "Cik Siti Wan Kembang",
        "Puteri Saadong",
        "Tun Fatimah",
    ),
    "maliens": (
        "Sogolon Kédjou",
        "Kankou Kanté",
        "Kamissa Camara",
    ),
    "mapuche": (
        "Janequeo",
        "Guacolda",
        "Fresia",
        "Tegualda",
    ),
    "mayas": (
        "Lady Xoc",
        "Sak K'uk'",
        "Ix Yohl Ik'nal",
        "Lady Six Sky",
    ),
    "mongols": (
        "Börte",
        "Chabi",
        "Töregene",
        "Sorghaghtani Beki",
        "Oghul Qaimish",
    ),
    "muisca": (
        "Usaca",
        "Machinza",
        "Zoratama",
        "Noncetá",
        "Magdalena de Guatavita",
    ),
    "perses": (
        "Atossa",
        "Parysatis",
        "Roshanak",
        "Shirin",
        "Purandokht",
    ),
    "polonais": (
        "Jadwiga d'Anjou",
        "Doubravka de Bohême",
        "Bona Sforza",
        "Anna Jagiellonka",
        "Élizabeth de Pologne",
    ),
    "portugais": (
        "Thérèse de León",
        "Isabelle de Portugal",
        "Jeanne de Portugal",
        "Béatrice de Portugal",
    ),
    "romains": (
        "Livie Drusilla",
        "Octavie la Jeune",
        "Julia Caesaris",
        "Agrippine la Jeune",
        "Faustine la Jeune",
    ),
    "sarrasins": (
        "Zénobie",
        "Shajar ad-Durr",
        "Zubaida bint Ja'far",
        "Arwa al-Sulayhi",
        "Aïcha bint Abi Bakr",
    ),
    "saxons": (
        "Mathilde de Ringelheim",
        "Edith d'Angleterre",
        "Ælfgifu",
        "Oda de Saxe",
    ),
    "shu": (
        "Sun Shangxiang",
        "Dame Gan",
        "Dame Mi",
        "Dame Zhang",
        "Dame Xiahou",
    ),
    "siciliens": (
        "Constance de Hauteville",
        "Adélaïde del Vasto",
        "Marguerite de Navarre",
        "Sibylle de Acerra",
    ),
    "slaves": (
        "Olga de Kiev",
        "Rogneda de Polotsk",
        "Dobrava de Bohême",
        "Anne de Kiev",
        "Jelena Balšić",
    ),
    "tatars": (
        "Töregene",
        "Oghul Qaimish",
        "Nur Sultan",
    ),
    "teutons": (
        "Mathilde de Toscane",
        "Adélaïde de Meissen",
        "Kunigonde de Hohenstaufen",
        "Adélaïde de Vohburg",
        "Béatrice de Souabe",
    ),
    "tupi": (
        "Bartira",
        "Paragaçu",
        "Aracy",
    ),
    "turcs": (
        "Kösem Sultan",
        "Hürrem Sultan",
        "Nurbanu Sultan",
        "Safiye Sultan",
        "Mihrimah Sultan",
    ),
    "varègues": (
        "Ingigerd Olofsdotter",
        "Ellisif de Kiev",
        "Astrid Tryggvesdatter",
        "Rogneda de Polotsk",
    ),
    "vikings": (
        "Aud Ketilsdóttir « la Profonde »",
        "Freydís Eriksdóttter",
        "Gunnhild Konungamóðir",
        "Sigrid la Haute",
        "Thyra Danebod",
    ),
    "wei": (
        "Dame Zhen",
        "Dame Bian",
        "Guo Nüwang",
        "Cao Jie",
        "Princesse Dongxiang",
    ),
    "wu": (
        "Dame Bu Lianshi",
        "Dame Xu",
        "Daqiao",
        "Xiaoqiao",
        "Sun Shangxiang",
    ),
}


def _pick_promise(seed: str, civilization: str) -> str | None:
    """Pick one stable historical spouse for a lord (PROMISES.md, D34).

    Pure role-play flavour (the designer's PROMISES.md table): the
    announcements and the Paroisse badges show « Thérèse de León
    (Portugais) » instead of a raw civilization name. The pick is
    SEEDED — the same lord wedded to the same civilization always
    shows the same promise in every view, without persisting
    anything on the lord document (a restart never changes a bride).
    """
    promises = _PROMISES.get(str(civilization).casefold())
    if not promises:
        return None
    rng = random.Random(str(seed))  # noqa: S311 - game draw, not crypto
    return rng.choice(promises)


class MarriageError(KingdomsModError):
    """Base of the marriage-domain errors."""

    code = "KINGDOMS_MARRIAGE_ERROR"
    message_key = "kingdoms.errors.marriage_unexpected"


class AlreadyMarriedError(MarriageError):
    """Raised when a lord weds twice (D34: one marriage per lord)."""

    code = "KINGDOMS_ALREADY_MARRIED"
    message_key = "kingdoms.errors.already_married"


class MarriageCapacityError(MarriageError):
    """Raised when the kingdom exceeds its marriage capacity (D53)."""

    code = "KINGDOMS_MARRIAGE_CAPACITY"
    message_key = "kingdoms.errors.marriage_capacity"


class UnknownCivilizationError(MarriageError):
    """Raised when the civilization is not in the catalog."""

    code = "KINGDOMS_UNKNOWN_CIVILIZATION"
    message_key = "kingdoms.errors.unknown_civilization"


class MarriageExclusivityError(MarriageError):
    """Raised when an active marriage already claims the civilization (D74)."""

    code = "KINGDOMS_MARRIAGE_EXCLUSIVITY"
    message_key = "kingdoms.errors.marriage_exclusivity"


class NoMarriagePointsError(MarriageError):
    """Raised when the kingdom holds no marriage point (D59)."""

    code = "KINGDOMS_NO_MARRIAGE_POINTS"
    message_key = "kingdoms.errors.no_marriage_points"


class NoArrangedTokenError(MarriageError):
    """Raised when the kingdom holds no arranged-marriage token (D60)."""

    code = "KINGDOMS_NO_ARRANGED_TOKEN"
    message_key = "kingdoms.errors.no_arranged_token"


class CivilizationNotInHandError(MarriageError):
    """Raised when a standard marriage targets an unheld civilization.

    The kingdom must hold the civilization in its Alliances hand
    (Drasah 2026-10-11); the arranged marriage carries no such rule.
    """

    code = "KINGDOMS_CIV_NOT_IN_HAND"
    message_key = "kingdoms.errors.civ_not_in_hand"


class RoyalMarriagePointsError(MarriageError):
    """Raised when a KING weds without the two points a Reine costs.

    Drasah's Reine rule (2026-10-11): a royal marriage (standard)
    consumes TWO points de mariage - the doubled price of the
    season-long security and the +2 tech reward.
    """

    code = "KINGDOMS_ROYAL_MARRIAGE_POINTS"
    message_key = "kingdoms.errors.royal_marriage_points"


class RoyalMarriageTokenError(MarriageError):
    """Raised when a KING weds arranged without the two tokens required.

    Drasah's Reine rule (2026-10-11): a royal arranged marriage
    consumes TWO arranged-marriage tokens.
    """

    code = "KINGDOMS_ROYAL_MARRIAGE_TOKEN"
    message_key = "kingdoms.errors.royal_marriage_token"


class DiplomacyService:
    """Civilization conditions and marriage rules (reference §18-§19)."""

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
    # Conditions engine (D32/D33)
    # ------------------------------------------------------------------
    async def recalculate(self) -> dict[str, list[str]]:
        """Recompute every kingdom's playable civilizations (cycle end).

        Civs come from the owned territories' cadastre, the unlock
        chains, the referential name rule and the marriages that secure
        them; a lost territory drops its civ on the next pass (D32).
        Drasah's 2026-10-11 Lord's Day rule: an un-wed drafted
        civilization is LOST at the recalculation — only a marriage
        keeps it; the cycle switch then draws a fresh draft per kingdom
        (:meth:`KingdomsService.redraw_lords_day_draft`).
        Lords of a kingdom flagged for a combat loss lose their marriage
        first (D34/D53). Returns the new lists for the diplomacy screen.
        """
        season = await self._require_season()
        kingdoms = await self._kingdoms.kingdoms()
        lords = await self._kingdoms.lords()
        reports: dict[str, list[str]] = {}
        dropped = list(season.pending_marriage_losses)
        for kingdom in kingdoms:
            if kingdom.is_gaia:
                continue
            owned = await self._owned_map_keys(kingdom.id)
            # the un-wed draft drops at the Lord's Day (Drasah's rule);
            # only condition-met and married civilizations survive here
            unlocked: list[str] = []
            for civ in self._config.civilizations:
                if self._condition_met(civ, kingdom, owned, unlocked):
                    if civ.key not in unlocked:
                        unlocked.append(civ.key)
            secured = [
                lord.married_civilization
                for lord in lords
                if lord.kingdom_id == kingdom.id
                and not lord.left
                and lord.married_civilization is not None
                # a royal marriage (Reine) survives every defeat —
                # Drasah's 2026-10-11 rule: secured for the whole season
                and (kingdom.id not in dropped or bool(lord.royal_marriage))
            ]
            for key in secured:
                if key not in unlocked:
                    unlocked.append(key)
            kingdom.civilizations = unlocked
            kingdom.secured_civilizations = [c for c in secured if c]
            await self._store.upsert_kingdom(kingdom.to_mongo())
            reports[kingdom.name] = unlocked
        if dropped:
            await self._apply_marriage_losses(lords, season, dropped)
        logger.info("kingdoms: alliances recomputed - %s", reports)
        return reports

    async def _apply_marriage_losses(
        self, lords: list[LordModel], season: SeasonState, dropped: list[str]
    ) -> None:
        """Drop the marriages of the defeated kingdoms (D34/D53).

        A royal marriage (Reine) never drops — Drasah's 2026-10-11
        rule: the civilization stays secured until the season ends.
        """
        for lord in lords:
            if (
                lord.kingdom_id in dropped
                and lord.married_civilization is not None
                and not lord.royal_marriage
            ):
                logger.info(
                    "kingdoms: %s loses the marriage %s (combat defeat)",
                    lord.id,
                    lord.married_civilization,
                )
                lord.married_civilization = None
                await self._store.upsert_lord(lord.to_mongo())
        season.pending_marriage_losses = []
        await self._store.upsert_season(season.to_mongo())

    def _is_unconditioned(self, civ: CivilizationCondition) -> bool:
        """Whether a catalog civ has no acquisition condition attached."""
        return not (
            civ.requires_map_keys
            or civ.requires_map_types
            or civ.requires_civilization is not None
            or civ.requires_kingdom_name_pattern is not None
        )

    def _condition_met(
        self,
        civ: CivilizationCondition,
        kingdom: KingdomModel,
        owned: list[str],
        unlocked: list[str],
    ) -> bool:
        """Evaluate one unlock condition (data, never code)."""
        if civ.requires_map_keys and any(key in owned for key in civ.requires_map_keys):
            return True
        if civ.requires_map_types and self._owns_map_type(kingdom.id, civ.requires_map_types):
            return True
        if civ.requires_civilization is not None:
            base = civ.requires_civilization
            return base in unlocked or base in kingdom.civilizations
        if civ.requires_kingdom_name_pattern is not None:
            return re.fullmatch(civ.requires_kingdom_name_pattern, kingdom.name) is not None
        return False

    async def _owns_map_type(self, kingdom_id: str, types: tuple[str, ...]) -> bool:
        """Whether the kingdom owns a territory of one of the map types."""
        territories = await self._territories.territories()
        for territory in territories:
            if territory.owner_kingdom_id != kingdom_id:
                continue
            entry = next(
                (item for item in self._config.maps if item.key == territory.map_key),
                None,
            )
            if entry is None:
                continue
            flags = entry.types.model_dump()
            if any(flags.get(name, False) for name in types):
                return True
        return False

    async def _owned_map_keys(self, kingdom_id: str) -> list[str]:
        """Return the map keys owned by the kingdom."""
        return [
            territory.map_key
            for territory in await self._territories.territories()
            if territory.owner_kingdom_id == kingdom_id
        ]

    # ------------------------------------------------------------------
    # Marriages (D34/D45/D53)
    # ------------------------------------------------------------------
    async def marry(
        self,
        player_id: str,
        civilization: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Marry a lord to a civilization (D34: one marriage per lord).

        The marriage costs ONE point de mariage of the kingdom's
        consumable stock (D59 — a currency, not a slot); the combat
        lock follows the Paroisse palier (24h Chapelle / 12h Église /
        6h Cathédrale, D64) and the Offrande of the palier credits the
        treasury at the celebration (Drasah 2026-10-11: +1 ⚙️ Église,
        +2 ⚙️ Cathédrale). The exclusivity is common to every marriage
        (D74): a civilization already claimed by an active marriage -
        any kingdom - is never targeted twice.
        """
        timestamp = now or game_now()
        season = await self._require_season()
        del season
        self._check_civilization(civilization)
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        if lord.married_civilization is not None:
            raise AlreadyMarriedError("a lord weds once per season (D34)")
        if lord.kingdom_id is None:
            raise MarriageCapacityError("the lord belongs to no kingdom")
        kingdom = await self._kingdom_by_id(lord.kingdom_id)
        if civilization not in kingdom.civilizations:
            # Drasah's 2026-10-11 rule: a standard marriage only weds a
            # civilization the kingdom currently holds in Alliances.
            raise CivilizationNotInHandError(
                f"{civilization} is not in the kingdom's hand (Alliances)"
            )
        royal = str(getattr(lord, "role", "")) == "king"
        cost = 2 if royal else 1
        if kingdom.marriage_points < cost:
            if royal:
                raise RoyalMarriagePointsError(
                    "a royal marriage (Reine) costs 2 marriage points"
                )
            raise NoMarriagePointsError("the kingdom holds no marriage point (D59)")
        await self._check_exclusivity(civilization)
        await self._commit_marriage(
            lord, kingdom, civilization, now=timestamp, royal=royal, point_cost=cost
        )
        logger.info("kingdoms: %s married %s", player_id, civilization)
        return civilization

    async def arranged_marriage(
        self,
        player_id: str,
        civilization: str,
        *,
        now: datetime | None = None,
    ) -> str:
        """Consume an arranged-marriage token (D60/D74): instant exclusivity.

        The token was bought at the Marché (3 techs, D60) and lives in
        the kingdom's ``arranged_marriage_stock``; this call consumes
        ONE token. The exclusivity is instant - the civilization is
        reserved to the kingdom right away - and the marriage points
        do NOT bind (D74: the stock is never consumed); the
        one-marriage-per-lord rule still applies (D45: never
        bypassed). Every check runs before the token is spent. A fresh
        arranged marriage locks the lord out of combat for 6 real
        hours (D60).
        """
        timestamp = now or game_now()
        self._check_civilization(civilization)
        lord = next(
            (item for item in await self._kingdoms.lords() if item.id == player_id),
            None,
        )
        if lord is None:
            raise KingdomNotFoundError("the player is not enrolled in the current season")
        if lord.married_civilization is not None:
            raise AlreadyMarriedError("a lord weds once per season (D45: never bypassed)")
        if lord.kingdom_id is None or lord.left:
            raise MarriageCapacityError("the lord belongs to no kingdom")
        kingdom = await self._kingdom_by_id(lord.kingdom_id)
        royal = str(getattr(lord, "role", "")) == "king"
        token_cost = 2 if royal else 1
        if kingdom.arranged_marriage_stock < token_cost:
            if royal:
                raise RoyalMarriageTokenError(
                    "a royal arranged marriage (Reine) costs 2 tokens"
                )
            raise NoArrangedTokenError("the kingdom holds no arranged-marriage token")
        await self._check_exclusivity(civilization)
        kingdom.arranged_marriage_stock -= token_cost
        await self._commit_marriage(
            lord,
            kingdom,
            civilization,
            hours=6,
            now=timestamp,
            royal=royal,
            point_cost=0,
        )
        logger.info("kingdoms: %s arranged-married %s", player_id, civilization)
        return civilization

    async def _check_exclusivity(self, civilization: str) -> None:
        """Refuse a civilization claimed by an active marriage (D74)."""
        for lord in await self._kingdoms.lords():
            if lord.married_civilization == civilization:
                raise MarriageExclusivityError(
                    f"an active marriage already claims {civilization} (D74)"
                )

    async def _commit_marriage(
        self,
        lord: LordModel,
        kingdom: KingdomModel,
        civilization: str,
        *,
        hours: int | None = None,
        now: datetime,
        royal: bool = False,
        point_cost: int | None = None,
    ) -> None:
        """Persist one marriage: points consumed, Offrande, secured civ, lock.

        ``hours=None`` means a standard marriage: the lock follows the
        Paroisse palier (D64) and the marriage points are consumed plus
        the palier's Offrande credited to the treasury. ``hours`` set
        (arranged) never touches the marriage points nor the Offrande.

        Drasah's Reine rule (2026-10-11): ``royal=True`` (a KING's
        wedding) doubles the combat lock, grants +2 tech points to the
        treasury and flags the lord ``royal_marriage`` — the
        civilization then survives every combat defeat until the
        season ends. ``point_cost`` overrides the standard point
        debit (arranged marriages pay nothing here).
        """
        tier = self._config.parish.tier(getattr(kingdom, "parish_tier", 1))
        lock_hours = tier.marriage_lock_hours if hours is None else hours
        if royal:
            lock_hours = lock_hours * 2
        if hours is None:
            cost = 1 if point_cost is None else max(0, point_cost)
            kingdom.marriage_points -= cost
            if tier.offering > 0:
                kingdom.tech_points_bank += tier.offering
                logger.info(
                    "kingdoms: Offrande - %s +%s tech (marriage of %s)",
                    kingdom.id,
                    tier.offering,
                    lord.id,
                )
        if royal:
            kingdom.tech_points_bank += 2
            logger.info(
                "kingdoms: royal marriage - %s +%s tech (Reine of %s)",
                kingdom.id,
                2,
                lord.id,
            )
        lord.married_civilization = civilization
        lord.royal_marriage = royal
        lord.marriage_locked_until = int((now + timedelta(hours=lock_hours)).timestamp())
        await self._store.upsert_lord(lord.to_mongo())
        if civilization not in kingdom.secured_civilizations:
            kingdom.secured_civilizations = [*kingdom.secured_civilizations, civilization]
        if civilization not in kingdom.civilizations:
            kingdom.civilizations = [*kingdom.civilizations, civilization]
        await self._store.upsert_kingdom(kingdom.to_mongo())

    async def record_defeat(self, kingdom_id: str) -> None:
        """Flag a combat defeat: the marriages drop at the next cycle (D34)."""
        season = await self._require_season()
        if kingdom_id not in season.pending_marriage_losses:
            season.pending_marriage_losses = [
                *season.pending_marriage_losses,
                kingdom_id,
            ]
            await self._store.upsert_season(season.to_mongo())
        logger.info("kingdoms: %s flagged for marriage loss at next cycle", kingdom_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _check_civilization(self, civilization: str) -> None:
        """Refuse civilizations outside the admin catalog."""
        if civilization not in {civ.key for civ in self._config.civilizations}:
            raise UnknownCivilizationError(f"unknown civilization: {civilization}")

    async def _kingdom_marriages_async(self, kingdom_id: str) -> int:
        """Count the active marriages of a kingdom."""
        return len(
            [
                lord
                for lord in await self._kingdoms.lords()
                if lord.kingdom_id == kingdom_id
                and not lord.left
                and lord.married_civilization is not None
            ]
        )

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
