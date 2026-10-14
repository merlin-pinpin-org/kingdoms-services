"""Kingdoms mod volière (voliere-v2, Drasah 14/10/2026) - unit tests.

Behind an in-memory store: the Féodal gate, the SLOTS BY AGE (Féodal 1,
Château 2, Impérial 3 — one bird per slot, no repeats), the WHOLE-BIRD
flight (one action and the bird is gone for its cooldown, the other
birds stay usable), the kingdom-SHARED cooldowns, the Corbeau draws
(civ exclusivity, cover-piercing verify, the 24h « suivi » delay), the
Faucon (not-yet-drawn territory, pick & ban sabotage with the 2-limit,
the Lâcher immune window) and the panel-side Pigeon spend (question/MP).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kingdoms.mods.kingdoms.attacks import EmbuscadeError, TechnologyError
from kingdoms.mods.kingdoms.config import CivilizationCondition, default_season_config
from kingdoms.mods.kingdoms.economy import EconomyService, TerritoryProtectedError
from kingdoms.mods.kingdoms.models import LordRole
from kingdoms.mods.kingdoms.voliere import (
    VoliereCooldownError,
    VoliereLockedError,
    VolierePoolEmptyError,
    VoliereService,
    VoliereTargetError,
)
from tests.mocks.discord_mock import MockInteraction, MockUser

from .test_kingdoms_attacks import Bundle

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
"""A Sunday 14:00 Paris moment — outside the Lord's Day window."""


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _NOW)


class VoliereBundle(Bundle):
    """The attack bundle extended with the volière and economy services."""

    def __init__(self) -> None:
        super().__init__(
            default_season_config().model_copy(
                update={
                    "kingdoms_count": 3,
                    "civilizations": tuple(
                        CivilizationCondition(key=f"civ-{i}", display_name=f"Civ {i}")
                        for i in range(6)
                    ),
                }
            )
        )
        self.voliere = VoliereService(
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )
        self.economy = EconomyService(
            self.store, self.config, self.kingdoms, self.territories, self.attacks
        )

    async def stock(self, kingdom_id: str, **purchases: int) -> None:
        """Give bought technologies to a kingdom."""
        from kingdoms.mods.kingdoms.models import TechnologyState

        state = TechnologyState(
            _id=kingdom_id, season_id="s", tech_points=0, purchases=dict(purchases)
        )
        await self.store.upsert_technology(state.to_mongo())

    async def enroll_third(self) -> str:
        """Enroll a third kingdom (Champagne) and return its id."""
        await self.kingdoms.enroll(
            "king-c", "King C", LordRole.KING, proposed_name="Champagne"
        )
        return await self.kingdom_id("Champagne")

    async def to_feudal(self) -> None:
        """Move the running season to the Âge Féodal (the volière gate)."""
        await self.set_age("feudal_age")

    async def set_age(self, age_key: str) -> None:
        """Move the running season to one age of the ladder."""
        season = await self.kingdoms.current_season()
        assert season is not None
        season.current_age_key = age_key
        await self.store.upsert_season(season.to_mongo())

    async def choose(self, kingdom_id: str, bird: str) -> str:
        """Fill one open slot with a bird (the v2 gate before any action)."""
        return await self.voliere.choose_bird(kingdom_id, bird, now=_NOW)

    async def land(self, kingdom_id: str, bird: str) -> None:
        """Force one bird back on its perch (test helper for 2-limit)."""
        kingdom = next(k for k in await self.kingdoms.kingdoms() if k.id == kingdom_id)
        cooldowns = dict(kingdom.voliere_cooldowns)
        cooldowns[bird] = 0
        kingdom.voliere_cooldowns = cooldowns
        await self.store.upsert_kingdom(kingdom.to_mongo())


# ----------------------------------------------------------------------
# The slots by age (voliere-v2)
# ----------------------------------------------------------------------


async def test_actions_are_locked_before_the_feudal_age() -> None:
    """voliere-v2: neither the slot choice nor an action before the Féodal."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    with pytest.raises(VoliereLockedError):
        await bundle.choose(aquitaine, "corbeau")
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.pick_civilization(
            aquitaine, "corbeau_civ", now=_NOW
        )


async def test_slots_follow_the_age_ladder() -> None:
    """Féodal opens 1 slot, Château 2, Impérial 3 — no more, no less."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.to_feudal()
    state = await bundle.voliere.slot_state(aquitaine)
    assert state["capacity"] == 1
    assert state["open"] == 1
    assert state["next_age"] == "castle_age"
    await bundle.choose(aquitaine, "corbeau")
    # The single Féodal slot is filled — a second bird waits for Château.
    with pytest.raises(VoliereLockedError):
        await bundle.choose(aquitaine, "faucon")
    await bundle.set_age("castle_age")
    await bundle.choose(aquitaine, "faucon")
    with pytest.raises(VoliereLockedError):
        await bundle.choose(aquitaine, "pigeon")
    await bundle.set_age("imperial_age")
    await bundle.choose(aquitaine, "pigeon")
    state = await bundle.voliere.slot_state(aquitaine)
    assert state["capacity"] == 3
    assert state["open"] == 0
    assert state["available"] == []


async def test_a_bird_can_never_be_taken_twice() -> None:
    """v2: an already-owned bird is refused (VoliereTargetError)."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.set_age("imperial_age")
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "corbeau")
    with pytest.raises(VoliereTargetError):
        await bundle.choose(aquitaine, "corbeau")
    with pytest.raises(VoliereTargetError):
        await bundle.choose(aquitaine, "condor")


async def test_actions_require_their_bird_in_a_slot() -> None:
    """v2: an action whose bird is not owned is refused (VoliereLockedError)."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "pigeon")
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.pick_civilization(aquitaine, "corbeau_civ", now=_NOW)
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.pick_territory(aquitaine, now=_NOW)


async def test_one_action_flies_the_whole_bird() -> None:
    """v2: after the civ draw, the Corbeau flies — the Pigeon stays usable."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.set_age("imperial_age")
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "corbeau")
    await bundle.choose(aquitaine, "pigeon")
    await bundle.voliere.pick_civilization(aquitaine, "corbeau_civ", now=_NOW)
    # The WHOLE Corbeau flies: verify is refused for 168h.
    with pytest.raises(VoliereCooldownError):
        await bundle.voliere.pick_civilization(
            aquitaine, "corbeau_civ", now=_NOW + timedelta(hours=1)
        )
    # The parallel Pigeon is untouched: its draw works at once.
    drawn = await bundle.voliere.pick_civilization(
        aquitaine, "pigeon_civ", now=_NOW + timedelta(hours=1)
    )
    assert drawn


# ----------------------------------------------------------------------
# The draws and effects (kept from v1, behind the v2 slots)
# ----------------------------------------------------------------------


async def test_pick_civ_excludes_hand_and_married() -> None:
    """The draw covers the catalog minus the kingdom's hand and the married."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "corbeau")
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    hand = set(kingdom.civilizations)
    drawn = await bundle.voliere.pick_civilization(aquitaine, "corbeau_civ", now=_NOW)
    assert drawn not in hand
    fresh = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    assert drawn in fresh.civilizations


async def test_cooldowns_are_shared_by_the_whole_kingdom() -> None:
    """Any lord may act — the cooldown gates the kingdom, not the lord."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "pigeon")
    await bundle.voliere.pick_civilization(aquitaine, "pigeon_civ", now=_NOW)
    with pytest.raises(VoliereCooldownError):
        await bundle.voliere.pick_civilization(
            aquitaine, "pigeon_civ", now=_NOW + timedelta(hours=1)
        )
    # 72h later (the pigeon cooldown) the draw opens again.
    drawn = await bundle.voliere.pick_civilization(
        aquitaine, "pigeon_civ", now=_NOW + timedelta(hours=73)
    )
    assert drawn


async def test_pick_territory_draws_a_not_yet_drawn_map() -> None:
    """The Faucon territory draw only covers maps not yet in game."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "faucon")
    before = {
        str(t.map_key) for t in await bundle.territories.territories()
    }
    drawn = await bundle.voliere.pick_territory(aquitaine, now=_NOW)
    assert drawn not in before
    fresh = next(
        t
        for t in await bundle.territories.territories()
        if str(t.map_key) == drawn
    )
    assert fresh.owner_kingdom_id == aquitaine


async def test_verify_pierces_the_cover() -> None:
    """The Corbeau verify reveals the REAL attacker, cover or not."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(bourgogne, "corbeau")
    await bundle.stock(aquitaine, contre_espionnage=1)
    target = (await bundle.owner_maps(bourgogne))[0]
    # Aquitaine declares under the cover of its second lord.
    attack = await bundle.attacks.declare_attack(
        "king-a", target, "aoe2de://x", cover_lord_id="lord-a", now=_NOW
    )
    assert attack.cover_lord_id is not None
    name = await bundle.voliere.verify_attack(bourgogne, attack.id, now=_NOW)
    assert name == "King A"


async def test_verify_refuses_foreign_attacks() -> None:
    """Only the DEFENDER may verify — another kingdom gets a target error."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(aquitaine, "corbeau")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x", now=_NOW)
    with pytest.raises(VoliereTargetError):
        await bundle.voliere.verify_attack(aquitaine, attack.id, now=_NOW)


async def test_tracked_lord_declares_with_the_24h_delay() -> None:
    """The « suivi » lord's declarations leave with the 24h delay."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "corbeau")
    await bundle.voliere.track_lord(aquitaine, "king-b", now=_NOW)
    assert await bundle.voliere.is_lord_tracked("king-b", _NOW)
    target = (await bundle.owner_maps(aquitaine))[0]
    attack = await bundle.attacks.declare_attack("king-b", target, "aoe2de://x", now=_NOW)
    assert attack.expires_at == _NOW + timedelta(hours=24)
    # The window lapses after 24h — the delay falls back to normal.
    assert not await bundle.voliere.is_lord_tracked(
        "king-b", _NOW + timedelta(hours=25)
    )


async def test_faucon_release_grants_the_24h_immunity() -> None:
    """The Lâcher blocks the Embuscade, the Traquenard and the Corruption."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(bourgogne, "faucon")
    until = await bundle.voliere.release_faucon(bourgogne, now=_NOW)
    assert until == int(_NOW.timestamp()) + 24 * 3600
    assert await bundle.voliere.has_faucon_release(bourgogne, _NOW)
    assert not await bundle.voliere.has_faucon_release(
        bourgogne, _NOW + timedelta(hours=25)
    )
    # Embuscade: refused against the released defender.
    target = (await bundle.owner_maps(bourgogne))[0]
    with pytest.raises(EmbuscadeError):
        await bundle.attacks.declare_embuscade(
            "king-a", target, "aoe2de://x", now=_NOW
        )
    # Traquenard: refused against the released kingdom.
    with pytest.raises(TechnologyError):
        await bundle.attacks.activate_traquenard("king-a", bourgogne, now=_NOW)
    # Corruption: refused against the released kingdom's territory.
    await bundle.economy.grant_tech_points(aquitaine, 10)
    victim_territory = next(
        t
        for t in await bundle.territories.territories()
        if t.owner_kingdom_id == bourgogne
    )
    with pytest.raises(TerritoryProtectedError):
        await bundle.economy.buy_corruption(
            aquitaine, victim_territory.id, now=_NOW
        )


async def test_faucon_sabotage_counts_in_the_two_limit() -> None:
    """The Faucon sabotage lands in the pick & ban side, up to 2."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.draw(2)
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.choose(aquitaine, "faucon")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://x", now=_NOW)
    attack.pickban = {"sent": True, "resolved_pickban": False}
    await bundle.store.upsert_attack(attack.to_mongo())
    civs = [civ.key for civ in bundle.config.civilizations]
    # Two sabotages — the test lands the faucon between the two cooldowns.
    await bundle.voliere.faucon_sabotage(aquitaine, attack.id, civs[0], now=_NOW)
    await bundle.land(aquitaine, "faucon")
    await bundle.voliere.faucon_sabotage(aquitaine, attack.id, civs[1], now=_NOW)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["attacker_sabotages"] == [civs[0], civs[1]]
    # The 2-limit is reached: a third sabotage is refused.
    await bundle.land(aquitaine, "faucon")
    with pytest.raises(VoliereTargetError):
        await bundle.voliere.faucon_sabotage(aquitaine, attack.id, civs[2], now=_NOW)
    # An outsider kingdom cannot sabotage an attack it is not part of.
    champagne = await bundle.enroll_third()
    await bundle.choose(champagne, "faucon")
    with pytest.raises(VoliereTargetError):
        await bundle.voliere.faucon_sabotage(champagne, attack.id, civs[0], now=_NOW)


async def test_pool_empty_when_everything_is_drawn() -> None:
    """Every map already in game — the Faucon territory draw is empty."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.choose(aquitaine, "faucon")
    for entry in bundle.config.maps:
        await bundle.territories.draw_map_for(aquitaine, entry.key)
    with pytest.raises(VolierePoolEmptyError):
        await bundle.voliere.pick_territory(aquitaine, now=_NOW)


# ----------------------------------------------------------------------
# The panel-side Pigeon spend (question / MP — voliere-v2)
# ----------------------------------------------------------------------


async def test_spend_action_gates_the_pigeon_flows() -> None:
    """The panel question/MP actions obey slots, cooldowns and the Féodal."""
    bundle = VoliereBundle()
    await bundle.launch_season()
    await bundle.to_feudal()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    # No pigeon in the volière — the panel spend is refused.
    with pytest.raises(VoliereLockedError):
        await bundle.voliere.spend_action(aquitaine, "pigeon_question", now=_NOW)
    await bundle.choose(aquitaine, "pigeon")
    await bundle.voliere.spend_action(aquitaine, "pigeon_question", now=_NOW)
    # The WHOLE pigeon flies: the anonymous MP is refused for 1h.
    with pytest.raises(VoliereCooldownError):
        await bundle.voliere.spend_action(
            aquitaine, "pigeon_dm", now=_NOW + timedelta(minutes=30)
        )
    await bundle.voliere.spend_action(
        aquitaine, "pigeon_dm", now=_NOW + timedelta(hours=1)
    )


# ----------------------------------------------------------------------
# The persistent button rebuild
# ----------------------------------------------------------------------


async def test_the_voliere_button_rebuilds_in_both_locales() -> None:
    """The restart-proof button rebuilds from its custom id (fr/en)."""
    import re

    from kingdoms.mods.kingdoms.kingdom_voliere_panel import KingdomVoliereButton

    match = re.match(r"kingdoms:voliere:open", "kingdoms:voliere:open")
    assert match is not None
    labels = []
    for locale in ("fr", "en"):
        interaction = MockInteraction(user=MockUser(id="u"), locale=locale)  # type: ignore[arg-type]
        button = await KingdomVoliereButton.from_custom_id(
            interaction, type("Item", (), {})(), match  # type: ignore[arg-type]
        )
        assert button.item.label
        labels.append(button.item.label)
    assert labels[0] != labels[1]  # fr and en labels differ
