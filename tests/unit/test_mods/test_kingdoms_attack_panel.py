"""Kingdoms mod attack panel Discord surface - unit tests (D77).

The pinned Attaquer panel, the persistent declare/defend buttons, the
declaration modal and the scheduler entry (reveal announcement, pick
& ban DM dispatch, forfeit announcements) behind the in-memory store.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import MockInteraction, MockUser

from .test_kingdoms_attack_phases import PhasesBundle

_SAFE_NOW = datetime(2026, 10, 11, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _freeze_attacks_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    from kingdoms.mods.kingdoms import attacks as attacks_module

    monkeypatch.setattr(attacks_module, "_now", lambda: _SAFE_NOW)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.edits: list[dict[str, Any]] = []
        self.deleted = False

    async def delete(self, *, delay: float | None = None) -> None:
        self.deleted = True
        return None

    async def edit(self, content: str | None = None, *, view: Any = None) -> None:
        self.edits.append({"content": content, "view": view})
        self.content = content if content is not None else self.content
        return None


class _History:
    def __init__(self, items: list[_FakeMessage]) -> None:
        self.items = items

    def __aiter__(self) -> _History:
        return self

    async def __anext__(self) -> _FakeMessage:
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)


class _FakeChannel:
    def __init__(self, name: str = "attaquer") -> None:
        self.name = name
        self.history_list: list[_FakeMessage] = []
        self.sent: list[Any] = []

    async def edit(self, **kwargs: Any) -> None:
        if "name" in kwargs:
            self.name = str(kwargs["name"])

    def history(self, limit: int = 50) -> _History:
        return _History(list(self.history_list))

    async def send(
        self, content: str | None = None, *, view: Any = None, **kwargs: Any
    ) -> Any:
        message = type("Sent", (), {"content": content, "view": view})()
        self.sent.append(message)
        return message


class _FakeGuild:
    def __init__(self, *channels: _FakeChannel) -> None:
        self.id = 1
        self.text_channels = list(channels)


class _FakeBot:
    def __init__(self, bundle: PhasesBundle, *channels: _FakeChannel) -> None:
        self.guilds = [_FakeGuild(*channels)]
        self.kingdoms_service = bundle.kingdoms
        self.kingdoms_attacks_service = bundle.attacks
        self.logs_service = None

    def get_user(self, user_id: str) -> Any:
        return None

    async def fetch_user(self, user_id: int) -> Any:
        raise RuntimeError("no user resolution in tests")


def _wiring(bundle: PhasesBundle) -> None:
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=bundle.kingdoms,
            attacks_service=bundle.attacks,
            territories_service=bundle.territories,
        )
    )


def test_panel_buttons_rebuild_from_the_wire_in_both_locales() -> None:
    """Check 30's lesson: the persistent labels exist in both locales."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        KingdomAttackDeclareButton,
        KingdomAttackDefendButton,
        _strings,
    )

    for locale in ("fr", "en"):
        strings = _strings(locale)
        declare = KingdomAttackDeclareButton(strings["declare_button"])
        assert declare.item.custom_id == "kingdoms:attack:declare"
        defend = KingdomAttackDefendButton("s1-a-t-king-a", strings["defend_button"])
        assert defend.item.custom_id == "kingdoms:attack:defend:s1-a-t-king-a"
        assert ATTACK_PANEL_MARKER


async def test_defend_button_rebuilds_with_the_attack_id() -> None:
    """The restart-proof rebuild keeps the embedded attack id."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton

    match: Any = re.match(
        r"kingdoms:attack:defend:(?P<attack_id>[A-Za-z0-9_:\-]+)",
        "kingdoms:attack:defend:s1-a-t-king-a",
    )
    button = await KingdomAttackDefendButton.from_custom_id(
        MockInteraction(locale="en-US"),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        match,  # type: ignore[arg-type]
    )
    assert button.attack_id == "s1-a-t-king-a"
    assert button.item.custom_id == "kingdoms:attack:defend:s1-a-t-king-a"


async def test_deploy_attack_panel_pins_marker_and_buttons() -> None:
    """The salon deployment pins the marker with a defend button per attack."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        deploy_attack_panel,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(20)
    attack = await bundle.fresh_attack()
    channel = _FakeChannel()
    # No marker in history: first deployment falls back to send.
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert len(channel.sent) == 1
    assert ATTACK_PANEL_MARKER in (channel.sent[0].content or "")
    ids = [item.custom_id for item in channel.sent[0].view.children]
    assert "kingdoms:attack:declare" in ids
    assert f"kingdoms:attack:defend:{attack.id}" in ids


async def test_deploy_attack_panel_edits_the_marked_message() -> None:
    """attaques-panel-spam: a found marker message is edited, never re-sent."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        deploy_attack_panel,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    channel = _FakeChannel()
    old = _FakeMessage(f"old stale panel\n{ATTACK_PANEL_MARKER}")
    channel.history_list = [old]
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert channel.sent == []  # no new message: the old one is edited in place
    assert len(old.edits) == 1
    assert ATTACK_PANEL_MARKER in (old.edits[0]["content"] or "")
    assert attack.map_key in (old.edits[0]["content"] or "")
    assert old.deleted is False


async def test_deploy_attack_panel_skips_unchanged_content() -> None:
    """attaques-panel-spam: identical content touches nothing at all."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _ongoing_rows,
        _strings,
        deploy_attack_panel,
        panel_text,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    await bundle.fresh_attack()
    channel = _FakeChannel()
    strings = _strings("fr")
    rows, _attacks = await _ongoing_rows(bundle.attacks, bundle.kingdoms, strings)
    old = _FakeMessage(panel_text(strings, rows))
    channel.history_list = [old]
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert channel.sent == []
    assert old.edits == []
    assert old.deleted is False


async def test_deploy_attack_panel_removes_duplicate_markers() -> None:
    """attaques-panel-spam: two markers keep the first, delete the rest."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        deploy_attack_panel,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    await bundle.fresh_attack()
    channel = _FakeChannel()
    first = _FakeMessage(f"panel A\n{ATTACK_PANEL_MARKER}")
    dupes = [_FakeMessage(f"panel B{i}\n{ATTACK_PANEL_MARKER}") for i in range(2)]
    channel.history_list = [first, *dupes]
    await deploy_attack_panel(channel, "fr", bundle.kingdoms, bundle.attacks)
    assert channel.sent == []
    assert len(first.edits) == 1
    assert all(d.deleted for d in dupes)


async def test_declare_flow_declares_the_attack() -> None:
    """The GUIDED flow (kingdom → territory → cover → lobby) declares (recâblage)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        KingdomAttackLobbyModal,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    # Step 1 — the kingdom select (Bourgogne visible with its AI level).
    kingdom_view = (click.response.message or None) and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [bourgogne]}, locale="fr"  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    assert step.response.message is not None
    # Step 2 — the territory select of the chosen kingdom.
    territory_view = step.response.message.view
    assert territory_view is not None
    territory_select = next(
        c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory"
    )
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [target]}, locale="fr"  # type: ignore[arg-type]
    )
    # Step 3 — no contre-espionnage stock: the lobby modal opens directly.
    await territory_select.callback(step2)  # type: ignore[arg-type]
    modal = step2.response.modal
    assert isinstance(modal, KingdomAttackLobbyModal)
    assert modal.map_key == target
    assert modal.cover_lord_id is None
    modal.lobby_input._value = "aoe2de://lobby"  # type: ignore[attr-defined]
    submit = MockInteraction(user=MockUser(id="king-a"), locale="fr")  # type: ignore[arg-type]
    await modal.on_submit(submit)  # type: ignore[arg-type]
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert str(attacks[0].state.value) == "declared"
    assert submit.followup.messages
    assert "Attaque déclarée" in (submit.followup.messages[0].content or "")


async def test_declare_flow_asks_the_cover_when_stock_exists() -> None:
    """The cover question appears only when a contre-espionnage charge exists."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(25)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    kingdom_view = click.response.message and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(
        c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom"
    )
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [bourgogne]}, locale="fr"  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    territory_select = next(
        c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory"
    )
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [target]}, locale="fr"  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # The cover question (OUI/NON) replaces the territory select.
    assert step2.response.message is not None
    cover_view = step2.response.message.view
    assert cover_view is not None
    ids = [c.custom_id for c in cover_view.children]
    assert "kingdoms:attack:cover:yes" in ids
    assert "kingdoms:attack:cover:no" in ids


async def test_declare_flow_attacks_gaia_through_the_free_for_all() -> None:
    """Drasah 15/10: Gaïa is attackable via the guided flow (no cover step)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        KingdomAttackLobbyModal,
        _strings,
    )
    from kingdoms.mods.kingdoms.models import AttackKind

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(26)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    # Step 1 — Gaïa appears in the kingdom select.
    kingdom_view = click.response.message and click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(
        c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom"
    )
    values = [o.value for o in kingdom_select.options]
    assert gaia in values
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [gaia]}, locale="fr"  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    # Step 2 — the Gaïa territories show up.
    territory_view = step.response.message and step.response.message.view
    assert territory_view is not None
    territory_select = next(
        c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory"
    )
    assert gaia_map in [o.value for o in territory_select.options]
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"), data={"values": [gaia_map]}, locale="fr"  # type: ignore[arg-type]
    )
    # Step 3 — a Gaïa target skips the cover question: lobby straight away.
    await territory_select.callback(step2)  # type: ignore[arg-type]
    modal = step2.response.modal
    assert isinstance(modal, KingdomAttackLobbyModal)
    assert modal.is_gaia is True
    modal.lobby_input._value = "aoe2de://gaia"  # type: ignore[attr-defined]
    submit = MockInteraction(user=MockUser(id="king-a"), locale="fr")  # type: ignore[arg-type]
    await modal.on_submit(submit)  # type: ignore[arg-type]
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert attacks[0].kind is AttackKind.GAIA
    assert attacks[0].participants == ["king-a"]
    assert submit.followup.messages
    assert "Attaque déclarée" in (submit.followup.messages[0].content or "")


async def test_join_button_adds_a_lord_to_the_gaia_battle() -> None:
    """Another lord joins the open Gaïa battle from the panel button."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackJoinButton, _strings

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(27)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    battle = await bundle.attacks.declare_gaia_attack("king-a", gaia_map, "aoe2de://g")
    assert battle.participants == ["king-a"]
    _wiring(bundle)
    button = KingdomAttackJoinButton(battle.id, _strings("fr")["gaia_join_button"])
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),  # type: ignore[arg-type]
        custom_id=f"kingdoms:attack:join:{battle.id}",
        locale="fr",
    )
    await button.callback(click)  # type: ignore[arg-type]
    assert click.followup.messages
    assert "rejoint la bataille" in (click.followup.messages[0].content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == battle.id)
    assert fresh.participants == ["king-a", "king-b"]
    # A kingdom joins a free-for-all once (D42): king-b cannot rejoin.
    again = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),  # type: ignore[arg-type]
        custom_id=f"kingdoms:attack:join:{battle.id}",
        locale="fr",
    )
    await button.callback(again)  # type: ignore[arg-type]
    assert again.followup.messages
    assert "❌" in (again.followup.messages[0].content or "")


async def test_defend_button_takes_the_defense() -> None:
    """The persistent defend button runs respond_defense (D77)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    attack = await bundle.fresh_attack()
    _wiring(bundle)
    button = KingdomAttackDefendButton(attack.id, _strings("fr")["defend_button"])
    interaction = MockInteraction(user=MockUser(id="lord-b"), locale="fr")  # type: ignore[arg-type]
    await button.callback(interaction)  # type: ignore[arg-type]
    assert interaction.followup.messages
    assert "défense de ton royaume" in (interaction.followup.messages[0].content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.defender_lord_id == "lord-b"


async def test_scheduler_run_announces_reveal_dm_and_forfeit() -> None:
    """The tick drives the phases: T-15 reveal, T-5 dispatch, T forfeit."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        run_attack_phases_safe,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    attack = await bundle.fresh_attack()
    declare_channel = _FakeChannel("Déclarer une attaque")
    announce_channel = _FakeChannel("Attaques")
    bot = _FakeBot(bundle, declare_channel, announce_channel)
    # T-16: nothing is due yet.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert not [m for m in announce_channel.sent if "défendu" in (m.content or "")]
    # T-2: the reveal runs — nobody defended, the AI takes over.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    channel = announce_channel
    assert any("IA" in (m.content or "") for m in channel.sent)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True
    assert fresh.pickban["sent"] is True  # the DM dispatch left (fail-safe here)
    assert any(
        ATTACK_PANEL_MARKER in (m.content or "") for m in declare_channel.sent
    )  # the panel re-pinned in the declare salon
    # T+1: the forfeit lands — the attacker missed his validation.
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 1, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert any("Forfait" in (m.content or "") for m in channel.sent)


async def test_scheduler_idle_tick_deploys_nothing() -> None:
    """attaques-panel-spam: an idle tick (no phase due) sends NOTHING."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    await bundle.fresh_attack()
    declare_channel = _FakeChannel("Déclarer une attaque")
    announce_channel = _FakeChannel("Attaques")
    bot = _FakeBot(bundle, declare_channel, announce_channel)
    # T-16: nothing is due — the tick must be totally silent.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert declare_channel.sent == []
    assert announce_channel.sent == []


async def test_scheduler_reveal_deploys_the_outcome_line() -> None:
    """attaques-panel-spam: at the T-15 reveal the panel shows the outcome."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        run_attack_phases_safe,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    declare_channel = _FakeChannel("Déclarer une attaque")
    announce_channel = _FakeChannel("Attaques")
    bot = _FakeBot(bundle, declare_channel, announce_channel)
    # Nobody defends: at T-2 the reveal runs, the AI takes over — the
    # panel line shows the « Assaut irrésistible — aucune défense » outcome.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    panels = [m for m in declare_channel.sent if ATTACK_PANEL_MARKER in (m.content or "")]
    assert panels
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True
    assert "Assaut irrésistible — aucune défense" in (panels[-1].content or "")

    # The defended case: another attack, a lord defends before the cutoff.
    bundle2 = PhasesBundle()
    await bundle2.launch_season()
    await bundle2.draw(25)
    attack2 = await bundle2.fresh_attack()
    # Back to the frozen clock (12:30 UTC, well before the 17:45 cutoff)
    # for the defense, then jump to T-2 for the reveal tick.
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    await bundle2.attacks.respond_defense(attack2.id, "lord-b")
    declare_channel2 = _FakeChannel("Déclarer une attaque")
    announce_channel2 = _FakeChannel("Attaques")
    bot2 = _FakeBot(bundle2, declare_channel2, announce_channel2)
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot2)
    panels2 = [m for m in declare_channel2.sent if ATTACK_PANEL_MARKER in (m.content or "")]
    assert panels2
    assert "Bataille en vue (défenseur" in (panels2[-1].content or "")
