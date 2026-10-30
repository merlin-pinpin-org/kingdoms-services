"""Kingdoms mod attack panel Discord surface - unit tests (D77).

The pinned Attaquer panel, the persistent declare/defend buttons, the
declaration modal and the scheduler entry (reveal announcement, pick
& ban DM dispatch, forfeit announcements) behind the in-memory store.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from kingdoms.mods.kingdoms.kingdom_persistent import (
    KingdomsPanelWiring,
    register_kingdoms_panel_wiring,
)
from tests.mocks.discord_mock import MockGuild, MockInteraction, MockTextChannel, MockUser

from .test_kingdoms_attack_phases import PhasesBundle
from .test_kingdoms_attack_score import ScoreBundle

_SAFE_NOW = datetime(2026, 10, 11, 17, 50, tzinfo=UTC)
"""Frozen INSIDE the [T-15, T] defense window (fresh attack T = 18:00)."""


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

    async def send(self, content: str | None = None, *, view: Any = None, **kwargs: Any) -> Any:
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
        _room_key,
        _strings,
    )

    for locale in ("fr", "en"):
        strings = _strings(locale)
        declare = KingdomAttackDeclareButton(strings["declare_button"])
        assert declare.item.custom_id == "kingdoms:attack:declare"
        defend = KingdomAttackDefendButton("s1-a-t-king-a", strings["defend_button"])
        # Check 73: the id rides a 16-hex digest (the raw ~64-char attack
        # id blew Discord's 100-char cap and killed the board edit).
        assert defend.item.custom_id == f"kingdoms:attack:defend:{_room_key('s1-a-t-king-a')}"
        assert len(str(defend.item.custom_id)) <= 100
        assert ATTACK_PANEL_MARKER


async def test_defend_button_rebuilds_with_the_attack_id() -> None:
    """The restart-proof rebuild resolves the digest back to the attack."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDefendButton,
        _room_key,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    attack = await bundle.fresh_attack()
    _wiring(bundle)
    custom_id = f"kingdoms:attack:defend:{_room_key(attack.id)}"
    assert len(custom_id) <= 100
    match: Any = re.match(
        r"kingdoms:attack:defend:(?P<key>[0-9a-f]{16})",
        custom_id,
    )
    assert match is not None
    button = await KingdomAttackDefendButton.from_custom_id(
        MockInteraction(locale="en-US"),  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        match,  # type: ignore[arg-type]
    )
    assert button.attack_id == attack.id
    assert button.item.custom_id == custom_id


async def test_deploy_attack_panel_pins_marker_and_buttons() -> None:
    """The declare salon stays CLEAN: marker + the declare button only."""
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
    # b: the declare + history + plan buttons only (the attacks live
    # in their own salons now — no rows, no defend button here), plus
    # the ⚔️ info button (attaque-ergonomie-v1: it replaced the
    # declaration thread).
    assert ids == [
        "kingdoms:attack:declare",
        "kingdoms:attack:history",
        "kingdoms:attack:plan",
        "kingdoms:attack:info",
    ]
    assert attack.map_key not in (channel.sent[0].content or "")
    # The pointer tells the players where the defenses live.
    assert "PROPRE salon" in (channel.sent[0].content or "")


async def test_declaration_opens_the_attack_salon() -> None:
    """restructuration-serveur-v1 b: every declaration opens a DEDICATED
    salon right under the declare salon — right name, everyone denied
    send (with the explicit guild.me overwrite, Check 75), the 🛡️
    Défendre + ❌ Annuler buttons on the base message, refs stored."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _open_attack_salon_safe,
        _room_key,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    _wiring(bundle)
    guild = MockGuild()
    declare = await guild.create_text_channel("Déclarer une attaque", position=3)
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.id != declare.id)
    # battle-ai-rdv-v1: the salon wears a readable 🗡️ duel name —
    # the ATTACKER VS the defender KINGDOM, slugified (the map title
    # lives in the announcement, never in the channel name).
    assert salon.name == "🗡️-king-a-vs-bourgogne"
    assert attack.map_key not in salon.name
    # Positioned right under the declare salon.
    assert salon.position == declare.position + 1
    # Check 75: the bot carries its own overwrite.
    # permissions-serveur-v1 (Drasah 19/10): @everyone is denied VIEW
    # too (a newcomer sees nothing) — the player roles carry the view.
    overwrites = dict(getattr(salon, "overwrites", {}) or {})
    everyone = overwrites.get(guild.default_role)
    assert everyone is not None
    assert everyone.send_messages is False
    assert everyone.view_channel is False
    mine = overwrites.get(guild.me)
    assert mine is not None
    assert mine.send_messages is True
    assert mine.view_channel is True
    # The base message carries the announcement (cover identity — the
    # salon name carries the map) + defend/cancel buttons.
    assert salon.messages
    base = salon.messages[0]
    assert "<@king-a>" in (base.content or "")  # no cover in fresh_attack
    assert "Bourgogne" in (base.content or "")
    ids = [str(c.custom_id) for c in base.view.children]
    assert f"kingdoms:attack:defend:{_room_key(attack.id)}" in ids
    assert f"kingdoms:attack:cancel:{_room_key(attack.id)}" in ids
    assert all(len(custom_id) <= 100 for custom_id in ids)
    # The refs route every later announcement to this salon.
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.score.get("attack_channel_id") == salon.id
    assert fresh.score.get("attack_base_message_id") == base.id


async def test_declaration_names_the_cover_not_the_attacker() -> None:
    """b: with an active cover the salon (and its announcement) shows
    the COVER lord — the real attacker stays masked until the reveal."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_attack_salon_safe, _strings
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(25)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    attack = await bundle.attacks.declare_attack(
        "king-a",
        target,
        "",
        cover_lord_id="lord-a",
        now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    )
    assert attack.cover_lord_id == "lord-a"
    _wiring(bundle)
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    # The salon name never leaks the real attacker — the cover identity
    # (lord-a is "Lord A", the masked king-a is "King A"),
    # slugified by the battle-room-id-v2 duel name.
    assert salon.name == "🗡️-lord-a-vs-bourgogne"
    assert "king-a" not in salon.name


async def test_declaration_paints_the_foreign_cover_kingdom_too() -> None:
    """bluff-externe-v1 4a (Drasah 29/10): the illusion wears the COVER
    lord's KINGDOM — a foreign cover paints the whole false flag; the
    real attacker's kingdom appears NOWHERE before the reveal."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_attack_salon_safe, _strings
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring
    from kingdoms.mods.kingdoms.models import LordRole

    bundle = PhasesBundle()
    await bundle.launch_season()
    # A third, foreign kingdom supplies the cover lord.
    await bundle.kingdoms.enroll("king-c", "King C", LordRole.KING, proposed_name="Provence")
    await bundle.draw(26)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    attack = await bundle.attacks.declare_attack(
        "king-a",
        target,
        "",
        cover_lord_id="king-c",
        now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    )
    assert attack.cover_lord_id == "king-c"
    _wiring(bundle)
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    # The salon name never leaks the real attacker.
    assert salon.name == "🗡️-king-c-vs-bourgogne"
    assert "king-a" not in salon.name
    # The announcement paints the WHOLE false flag: the cover lord AND
    # his kingdom — never the real attacker or Aquitaine.
    announce = str(salon.messages[0].content or "")
    assert "<@king-c>" in announce
    assert "**Provence**" in announce
    assert "king-a" not in announce
    assert "Aquitaine" not in announce


async def test_cover_select_offers_every_lord_except_the_attacked_kingdom() -> None:
    """bluff-externe-v1 (Drasah 29/10): after OUI the cover select lists
    every active lord — the foreign kingdoms included — EXCEPT the
    lords of the attacked kingdom; each option carries its kingdom as
    the description."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )
    from kingdoms.mods.kingdoms.models import LordRole

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.kingdoms.enroll("king-c", "King C", LordRole.KING, proposed_name="Provence")
    await bundle.kingdoms.enroll("lord-c", "Lord C", LordRole.LORD, kingdom_name="Provence")
    await bundle.draw(27)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, contre_espionnage=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",  # type: ignore[arg-type]
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    kingdom_view = click.response.message and click.response.message.view
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_select = next(
        c for c in step.response.message.view.children if c.custom_id == "kingdoms:attack:choose:territory"
    )
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    cover_view = step2.response.message.view
    yes = next(c for c in cover_view.children if c.custom_id == "kingdoms:attack:cover:yes")
    oui = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        locale="fr",  # type: ignore[arg-type]
    )
    await yes.callback(oui)  # type: ignore[arg-type]
    select = next(c for c in oui.response.message.view.children if c.custom_id == "kingdoms:attack:choose:cover")
    values = [o.value for o in select.options]
    # Every active lord EXCEPT the attacker and the attacked kingdom.
    assert "lord-a" in values  # the attacker's own kingdom stays eligible
    assert "king-c" in values and "lord-c" in values  # the foreign kingdom
    assert "king-b" not in values and "lord-b" not in values  # the attacked kingdom
    descriptions = {o.value: str(o.description or "") for o in select.options}
    assert descriptions["king-c"] == "Provence"
    assert descriptions["lord-a"] == "Aquitaine"


async def test_cancel_attack_button_refunds_and_purges_the_salon() -> None:
    """b: the DECLARANT alone, inside the 10-minute window — budgets
    refunded (attacker AND defender when recorded), the salon purged."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackCancelButton,
        _open_attack_salon_safe,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    # A defense is already recorded — its budget is refunded too.
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    attacker = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "king-a")
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert attacker.attack_used == 1
    assert defender.defense_used == 1
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    _wiring(bundle)
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    button = KingdomAttackCancelButton(attack.id, _strings("fr")["cancel_attack_button"])
    # A NON-declarant is refused.
    stranger = MockInteraction(user=MockUser(id="king-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(stranger)  # type: ignore[arg-type]
    assert stranger.followup.messages
    assert "❌" in str(stranger.followup.messages[0].content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert str(fresh.state.value) != "cancelled"
    # The declarant cancels inside the window — budgets refunded, salon gone.
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    assert click.followup.messages
    assert "annulée" in str(click.followup.messages[0].content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert str(fresh.state.value) == "cancelled"
    assert fresh.resolved_at is not None
    assert fresh.is_over is True
    attacker = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "king-a")
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert attacker.attack_used == 0
    assert defender.defense_used == 0
    assert guild.get_channel(salon.id) is None  # purged right away
    assert (fresh.score or {}).get("attack_salon_cleaned") is True


async def test_cancel_attack_refuses_after_the_window_and_the_reveal() -> None:
    """b: the 10-minute window closes, and a revealed attack (cover
    dropped, siege engaged) can no longer be cancelled."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.attacks import AttackStateError

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(attack.id, "king-a")
    revealed = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    revealed.revealed = True
    await bundle.store.upsert_attack(revealed.to_mongo())
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(attack.id, "king-a", now=datetime(2026, 10, 11, 12, 5, tzinfo=UTC))


async def test_cancel_defense_withdraws_and_refunds_before_the_draft() -> None:
    """b: the defender withdraws at any moment EXCEPT once the draft
    started (pick_ban_lead_minutes threshold) — budget refunded."""
    from kingdoms.mods.kingdoms.attacks import AttackStateError

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    # Only the defender may withdraw.
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_defense(attack.id, "king-a", now=datetime(2026, 10, 11, 12, 35, tzinfo=UTC))
    withdrawn = await bundle.attacks.cancel_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 35, tzinfo=UTC))
    assert str(withdrawn.state.value) == "declared"
    assert withdrawn.defender_lord_id is None
    defender = next(lord for lord in await bundle.kingdoms.lords() if str(lord.id) == "lord-b")
    assert defender.defense_used == 0
    # The defense may be retaken after a withdrawal.
    retaken = await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 13, 0, tzinfo=UTC))
    assert str(retaken.state.value) == "defended"
    # Once the draft lead starts (T - pick_ban_lead_minutes), refused.
    draft_start = attack.expires_at - timedelta(minutes=bundle.attacks._config.attacks.pick_ban_lead_minutes)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_defense(attack.id, "lord-b", now=draft_start + timedelta(minutes=1))


async def test_defend_button_answers_the_surprise_cases() -> None:
    """b: the 🛡️ button NEVER disappears — wrong kingdom, first defense
    (no public sign) and already-defended answers."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    before = len(salon.messages)
    button = KingdomAttackDefendButton(attack.id, _strings("fr")["defend_button"])
    # A lord OUTSIDE the defending kingdom is refused.
    outsider = MockInteraction(user=MockUser(id="lord-a"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(outsider)  # type: ignore[arg-type]
    assert "Vous ne pouvez défendre" in str(outsider.response.message.content)
    # The first lord of the defending kingdom registers the defense —
    # the salon base message does NOT move (no public sign).
    click = MockInteraction(user=MockUser(id="lord-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    assert "défense de ton royaume" in str(click.followup.messages[0].content)
    assert len(salon.messages) == before
    # Another lord of the same kingdom: « Le royaume est déjà défendu ».
    again = MockInteraction(user=MockUser(id="king-b"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(again)  # type: ignore[arg-type]
    assert "déjà défendu" in str(again.response.message.content)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.defender_lord_id == "lord-b"
    # defend-probe-leak (Drasah 29/10): the outsider clicks AGAIN on
    # the now-defended attack — he still gets the GENERIC refusal, the
    # « déjà défendu » answer must never leak to a foreign lord.
    probe = MockInteraction(user=MockUser(id="lord-a"), guild=guild, locale="fr")  # type: ignore[arg-type]
    await button.callback(probe)  # type: ignore[arg-type]
    assert "Vous ne pouvez défendre" in str(probe.response.message.content)
    assert "déjà défendu" not in str(probe.response.message.content)


async def test_history_button_lists_resolved_confrontations() -> None:
    """b: the 📜 button shows the RESOLVED confrontations (timestamped,
    paginated) — cancelled, won, expired alike."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackHistoryButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    await bundle.attacks.cancel_attack(attack.id, "king-a")
    _wiring(bundle)
    button = KingdomAttackHistoryButton(_strings("fr")["history_button"])
    assert button.item.custom_id == "kingdoms:attack:history"
    click = MockInteraction(user=MockUser(id="king-a"), locale="fr")  # type: ignore[arg-type]
    await button.callback(click)  # type: ignore[arg-type]
    content = str(click.response.message.content)
    assert "Historique" in content
    assert "annulée" in content
    assert attack.map_key in content


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
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    assert step.response.message is not None
    # Step 2 — the territory select of the chosen kingdom.
    territory_view = step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    # Step 3 — no contre-espionnage stock: the attack declares
    # DIRECTLY (Drasah 18/10 — no modal: an empty modal is rejected
    # by Discord and killed the flow, live beta 18/10).
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # battle-ai-rdv-v1: the map is chosen — the SELECTOR ephemeral dies
    # instantly (no stale wizard left behind after the declaration).
    assert click.original_deleted is True
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert str(attacks[0].state.value) == "declared"
    assert attacks[0].map_key == target
    assert attacks[0].lobby_url == ""
    assert step2.response.deferred is True
    assert step2.followup.messages
    assert "Attaque déclarée" in (step2.followup.messages[0].content or "")


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
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # The cover question (OUI/NON) replaces the territory select.
    assert step2.response.message is not None
    cover_view = step2.response.message.view
    assert cover_view is not None
    ids = [c.custom_id for c in cover_view.children]
    assert "kingdoms:attack:cover:yes" in ids
    assert "kingdoms:attack:cover:no" in ids


async def test_declare_flow_skips_the_cover_silently_when_no_lord_is_eligible() -> None:
    """bluff-externe-v1 5a (Drasah 29/10): with NO eligible cover
    candidate the question is skipped and the attack declares openly
    and SILENTLY — no note, no charge consumed (the note of the solo
    case was information pollution on every attack)."""
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
    # No eligible candidate: the only other Aquitaine lord left the
    # season and the remaining lords all belong to the ATTACKED
    # kingdom (Bourgogne) — excluded by the bluff-externe-v1 rule.
    lord_a = next(item for item in await bundle.kingdoms.lords() if str(item.id) == "lord-a")
    lord_a.left = True
    await bundle.store.upsert_lord(lord_a.to_mongo())
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
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    # Solo: the attack declares DIRECTLY — no OUI/NON question…
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert attacks[0].cover_lord_id is None
    assert step2.response.deferred is True
    assert not step2.response.message
    # …and NO note at all: only the itinerary confirmation follows
    # (the only lords left belong to the ATTACKED kingdom — excluded
    # by rule, and saying why would be information pollution).
    bodies = [str(m.content or "") for m in step2.followup.messages]
    assert any("Attaque déclarée" in body for body in bodies)
    assert all("bluff" not in body for body in bodies)
    assert all("seigneur actif" not in body for body in bodies)
    fresh_state = await bundle.attacks.technology_state(aquitaine)
    assert fresh_state.purchases.get("contre_espionnage") == 1


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
    kingdom_select = next(c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom")
    values = [o.value for o in kingdom_select.options]
    assert gaia in values
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [gaia]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    # Step 2 — the Gaïa territories show up.
    territory_view = step.response.message and step.response.message.view
    assert territory_view is not None
    territory_select = next(c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory")
    assert gaia_map in [o.value for o in territory_select.options]
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [gaia_map]},
        locale="fr",  # type: ignore[arg-type]
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
    assert len(str(button.item.custom_id)) <= 100  # Check 73: digest, not raw id
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),  # type: ignore[arg-type]
        custom_id=str(button.item.custom_id),
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
        custom_id=str(button.item.custom_id),
        locale="fr",
    )
    await button.callback(again)  # type: ignore[arg-type]
    assert again.followup.messages
    assert "❌" in (again.followup.messages[0].content or "")


async def test_gaia_declaration_opens_its_salon_and_is_never_cancellable() -> None:
    """restructuration-serveur-v1 b: a Gaïa FFA gets the SAME salon flow —
    🦅 Rejoindre + ❌ Annuler on the base message, the join announces
    IN the salon, and the service REFUSES a Gaïa cancellation (axis
    ouvert Drasah — default followed)."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.attacks import AttackStateError
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackJoinButton,
        _open_attack_salon_safe,
        _room_key,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = PhasesBundle()
    _, _, gaia = await bundle.launch_season()
    await bundle.draw(28)
    gaia_map = (await bundle.owner_maps(gaia))[0]
    battle = await bundle.attacks.declare_gaia_attack(
        "king-a", gaia_map, "aoe2de://g", now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    )
    _wiring(bundle)
    guild = MockGuild()
    declare = await guild.create_text_channel("Déclarer une attaque")
    await _open_attack_salon_safe(guild, _panel_wiring(), _strings("fr"), battle)
    salon = next(c for c in guild.text_channels if c.id != declare.id)
    # battle-ai-rdv-v1: the gaia salon wears the 🗡️ duel name — the
    # attacker VS « Gaïa » (the map title stays in the announcement).
    assert salon.name == "🗡️-king-a-vs-gaia"
    ids = [str(c.custom_id) for c in salon.messages[0].view.children]
    assert f"kingdoms:attack:join:{_room_key(battle.id)}" in ids  # 🦅 Rejoindre
    assert f"kingdoms:attack:cancel:{_room_key(battle.id)}" in ids  # ❌ Annuler
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == battle.id)
    assert fresh.score.get("attack_channel_id") == salon.id

    # The join announce lands in the salon.
    button = KingdomAttackJoinButton(battle.id, _strings("fr")["gaia_join_button"])
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-b"),
        guild=guild,
        locale="fr",  # type: ignore[arg-type]
    )
    await button.callback(click)  # type: ignore[arg-type]
    join_announces = [m for m in salon.messages if "rejoint la bataille" in (m.content or "")]
    assert join_announces  # the join is announced in the salon

    # The service REFUSES a Gaïa cancellation — even the declarant,
    # even inside the window (axis ouvert, default followed).
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 5, tzinfo=UTC)
    with pytest.raises(AttackStateError):
        await bundle.attacks.cancel_attack(battle.id, "king-a")


async def test_defend_button_takes_the_defense() -> None:
    """The persistent defend button runs respond_defense (D77)."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomAttackDefendButton, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(22)
    attack = await bundle.fresh_attack()
    # Drasah 18/10: the defense window opens AT the declaration — the
    # frozen clock sits inside it (12:30, declared at 12:00).
    attacks_module._now = lambda: datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    _wiring(bundle)
    button = KingdomAttackDefendButton(attack.id, _strings("fr")["defend_button"])
    interaction = MockInteraction(user=MockUser(id="lord-b"), locale="fr")  # type: ignore[arg-type]
    await button.callback(interaction)  # type: ignore[arg-type]
    assert interaction.followup.messages
    assert "défense de ton royaume" in (interaction.followup.messages[0].content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.defender_lord_id == "lord-b"


async def _salon_guild(bundle: PhasesBundle, attack: Any) -> MockGuild:
    """Build a MockGuild with the declare salon + the attack's OWN salon (b)."""
    guild = MockGuild()
    await guild.create_text_channel("Déclarer une attaque")
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    return guild


class _GuildBot:
    """A bot whose guilds are MockGuild instances (salon-based ticks)."""

    def __init__(self, bundle: PhasesBundle, guild: MockGuild) -> None:
        self.guilds = [guild]
        self.kingdoms_service = bundle.kingdoms
        self.kingdoms_attacks_service = bundle.attacks
        self.logs_service = None

    def get_user(self, user_id: str) -> Any:
        return None

    async def fetch_user(self, user_id: int) -> Any:
        raise RuntimeError("no user resolution in tests")


async def test_scheduler_reveals_then_the_ai_takes_over_at_the_meeting_time() -> None:
    """The tick drives the phases: T-15 cover drop, T the AI takeover + RDV."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        ATTACK_PANEL_MARKER,
        run_attack_phases_safe,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    declare_channel = guild.text_channels[0]
    bot = _GuildBot(bundle, guild)
    # T-16: nothing is due yet — the tick stays silent.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert salon.messages == []
    # T-2: the reveal runs IN THE SALON — the cover drops, the defense
    # stays OPEN (no AI).
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert not [m for m in salon.messages if "IA" in (m.content or "")]  # no AI at the cutoff
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.revealed is True
    assert fresh.ai_defender is False
    assert any("Assaut irrésistible" in (m.content or "") for m in salon.messages)
    assert any(
        ATTACK_PANEL_MARKER in (m.content or "") for m in declare_channel.messages
    )  # the panel re-pinned in the declare salon
    # T+1: the meeting time — nobody defended, the bot AI takes the
    # defense (battle-ai-rdv-v1): the draft RUNS even solo — the
    # attacker's DM leaves and the AI arms its random picks + delay.
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 1, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert any("IA" in (m.content or "") for m in salon.messages)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True
    assert not (fresh.pickban or {}).get("sent")  # the draft leaves on the NEXT tick
    # T+2: the tick sends the attacker's draft DM — the AI arms its 3
    # RANDOM picks of its hand + its delayed validation (the
    # resolution waits for the attacker's ranking AND the AI's mark).
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    await bundle.give_civs("Bourgogne", "huns", "perses", "byzantins")
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 2, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.pickban["sent"] is True  # the draft DM leaves at once
    assert not fresh.pickban.get("resolved_pickban")
    assert len(fresh.pickban["defender_picks"]) == 3
    assert 30 <= fresh.pickban["ai_validate_at"] - int(datetime(2026, 10, 11, 18, 2, tzinfo=UTC).timestamp()) <= 90
    assert not [m for m in salon.messages if "Forfait" in (m.content or "")]


async def test_scheduler_idle_tick_deploys_nothing() -> None:
    """attaques-panel-spam: an idle tick (no phase due) sends NOTHING."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(23)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    declare_channel = guild.text_channels[0]
    bot = _GuildBot(bundle, guild)
    # T-16: nothing is due — the tick must be totally silent.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 44, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert declare_channel.messages == []
    assert salon.messages == []


async def test_scheduler_reveal_names_the_defender_in_the_salon() -> None:
    """restructuration-serveur-v1 b: the salon replaces the Attaques board.

    At the T-15 reveal the cover drops and the outcome line lands in the
    attack's own salon: « Assaut irrésistible » when nobody defended,
    « Bataille en vue (défenseur : X) » — NAMING the human defender at
    last (Drasah 19/10) — when one answered.
    """
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(24)
    attack = await bundle.fresh_attack()
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    bot = _GuildBot(bundle, guild)
    # T-2: the reveal runs (cover drop + outcome line) — nobody
    # defended yet, so the salon shows the irresistible assault.
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.revealed is True
    assert fresh.ai_defender is False
    assert any("Assaut irrésistible — aucune défense" in (m.content or "") for m in salon.messages)
    # T+1: the meeting time — nobody defended, the bot AI takes the
    # defense; the announce fires in the salon.
    attacks_module._now = lambda: datetime(2026, 10, 11, 18, 1, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    assert any("IA" in (m.content or "") for m in salon.messages)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.ai_defender is True

    # The defended case: another attack, a lord defends right after
    # the declaration, then the reveal tick shows the battle — with
    # the defender NAMED at last (restructuration-serveur-v1 b).
    bundle2 = PhasesBundle()
    await bundle2.launch_season()
    await bundle2.draw(25)
    attack2 = await bundle2.fresh_attack()
    await bundle2.attacks.respond_defense(attack2.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))
    guild2 = await _salon_guild(bundle2, attack2)
    salon2 = next(c for c in guild2.text_channels if c.name != "Déclarer une attaque")
    bot2 = _GuildBot(bundle2, guild2)
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot2)
    battle = [m for m in salon2.messages if "Bataille en vue" in (m.content or "")]
    assert battle
    assert "défenseur : Lord B" in (battle[-1].content or "")


async def test_scheduler_reveal_clears_the_whole_false_flag() -> None:
    """bluff-externe-v1 (Drasah 29/10): at the T-15 reveal the salon
    message puts EVERYTHING clear — the real attacker AND his kingdom,
    under the cover of whom and of which kingdom. Zéro assistance
    elsewhere: the salon of the attack is the ONLY place it lands."""
    from kingdoms.mods.kingdoms import attacks as attacks_module
    from kingdoms.mods.kingdoms.kingdom_attack_panel import run_attack_phases_safe
    from kingdoms.mods.kingdoms.models import LordRole

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.kingdoms.enroll("king-c", "King C", LordRole.KING, proposed_name="Provence")
    await bundle.draw(28)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    await bundle.stock(aquitaine, contre_espionnage=1)
    attack = await bundle.attacks.declare_attack(
        "king-a",
        (await bundle.owner_maps(bourgogne))[0],
        "",
        cover_lord_id="king-c",
        now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    )
    guild = await _salon_guild(bundle, attack)
    salon = next(c for c in guild.text_channels if c.name != "Déclarer une attaque")
    bot = _GuildBot(bundle, guild)
    attacks_module._now = lambda: datetime(2026, 10, 11, 17, 58, tzinfo=UTC)
    await run_attack_phases_safe(bot)
    reveal = [m for m in salon.messages if "Reveal" in (m.content or "")]
    assert reveal
    content = str(reveal[-1].content or "")
    assert "King A" in content  # the REAL attacker
    assert "Aquitaine" in content  # his real kingdom
    assert "King C" in content  # the cover lord
    assert "Provence" in content  # the cover kingdom
    assert "La couverture tombe" in content


async def test_pickban_view_selects_acknowledge_and_validate() -> None:
    """Live beta 18/10: the pick & ban selects carry a callback — the
    acknowledged interaction stores the values on the view instance."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomPickBanView

    view = KingdomPickBanView(
        "s-20261018-015707-a-t-steppe-king-a-1791720000",
        "king-a",
        "attacker",
        ["francs", "bretons", "goths"],
        "fr",
    )
    selects = [c for c in view.children if getattr(c, "custom_id", "").startswith("kingdoms:attack:pickban:pick:")]
    assert len(selects) == 3
    for select in selects:
        assert callable(select.callback)  # « the application did not respond » fix
    validate = next(c for c in view.children if getattr(c, "custom_id", "") == "kingdoms:attack:pickban:validate")
    assert callable(validate.callback)
    # Drasah 18/10 v2: no sabotage select anymore — the moves ride the
    # battle room, and the resolution is BLIND (no adversary hand here).
    assert not [c for c in view.children if "sabotage" in str(getattr(c, "custom_id", ""))]
    assert len(view.children) == 4  # 3 picks + validate


async def test_pickban_validate_rejects_duplicates_and_missing_picks() -> None:
    """pickban-doublon-v1 (Drasah 28/10): a DUPLICATE answers the dedicated
    wording (« vous ne pouvez pas avoir de doublons »), a MISSING pick keeps
    the old « Classe d'abord » one — the two cases are split."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomPickBanView
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    register_kingdoms_panel_wiring(KingdomsPanelWiring(attacks_service=object()))
    view = KingdomPickBanView("a-1", "king-a", "attacker", ["francs", "bretons", "goths"], "fr")

    # Duplicates: the same civilization twice → the dedicated message.
    for select, vals in zip(view.rank_selects, [["francs"], ["francs"], []]):
        select.values = vals  # type: ignore[misc]
    click = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await view._on_validate(click)  # type: ignore[arg-type]
    assert click.response.ephemeral is True
    content = str(click.response.message.content or "") if click.response.message else ""
    assert "doublons dans vos choix de civilisations" in content
    assert "Classe d'abord" not in content

    # A missing pick keeps the old wording.
    for select, vals in zip(view.rank_selects, [["francs"], [], []]):
        select.values = vals  # type: ignore[misc]
    click2 = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await view._on_validate(click2)  # type: ignore[arg-type]
    content2 = str(click2.response.message.content or "") if click2.response.message else ""
    assert "Classe d'abord tes 3 civilisations" in content2


def test_room_dm_carries_voice_salon_and_time_window() -> None:
    """room-dm-v2 (Drasah 28/10, sa maquette): « Tiens-toi prêt » — the DM
    points at the voice channel AND the attack salon, then carries the
    presence time window (the T+5 forfait line) and the encouragement."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _strings

    fr = _strings("fr")
    dm = fr["room_dm"].format(map="fortress", room="<#10>", salon="<#20>")
    dm += fr["room_dm_deadline"].format(deadline="14h30")
    dm += fr["room_dm_luck"]
    assert "Tiens-toi prêt — fortress" in dm
    assert "<#10>" in dm and "<#20>" in dm
    assert "avant 14h30" in dm and "forfait" in dm
    assert "Bonne chance" in dm

    en = _strings("en")
    dm_en = en["room_dm"].format(map="fortress", room="<#10>", salon="<#20>")
    dm_en += en["room_dm_deadline"].format(deadline="14:30")
    dm_en += en["room_dm_luck"]
    assert "Stand ready — fortress" in dm_en
    assert "before 14:30" in dm_en and "forfeit" in dm_en
    assert "Good luck" in dm_en


async def test_room_and_ready_custom_ids_fit_the_discord_limit() -> None:
    """Live beta 18/10: the RDV DM died with 50035 (custom_id > 100) —
    the persistent ids must stay short whatever the attack id length."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomResultButton,
        KingdomRoomUrlButton,
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
        _strings,
    )

    attack_id = "s-20261018-015707-a-s-20261018-015707-t-plaine-king-a-1791720000"
    player_id = "242421489536729100"
    strings = _strings("fr")
    ready = KingdomSabotageOpenButton(attack_id, player_id, strings["sab_open_button"])
    passer = KingdomSabotagePassButton(attack_id, player_id, strings["sab_pass_button"])
    url = KingdomRoomUrlButton(attack_id, strings["score_url_button"])
    result = KingdomRoomResultButton(attack_id, strings["result_button"])
    ids = [str(ready.custom_id), str(passer.custom_id), str(url.custom_id), str(result.custom_id)]
    for custom_id in ids:
        assert 1 <= len(custom_id) <= 100
    assert len(set(ids)) == 4
    # Restart-stable: the same inputs rebuild the same ids (bot.add_view
    # re-registration must keep answering).
    twin = [
        KingdomSabotageOpenButton(attack_id, player_id, strings["sab_open_button"]),
        KingdomSabotagePassButton(attack_id, player_id, strings["sab_pass_button"]),
        KingdomRoomUrlButton(attack_id, strings["score_url_button"]),
        KingdomRoomResultButton(attack_id, strings["result_button"]),
    ]
    assert [str(item.custom_id) for item in twin] == ids
    # The key is a 16-hex digest — the raw attack id never rides the id.
    assert all(
        re.fullmatch(r"[0-9a-f]{16}", part)
        for part in (ready.custom_id.split(":")[-1], passer.custom_id.split(":")[-1], url.custom_id.split(":")[-1])
    )


async def test_score_due_opens_the_battle_room_with_shuffled_civs() -> None:
    """Drasah 18/10 v2: at T the meeting opens a battle room — role,
    voice channel under the provisioned « Champs de Bataille » category
    (battle-room-categorie-v1: the category is NEVER created here), a
    text-in-voice message with each player's SHUFFLED civs + the
    URL/result buttons, and the room refs stored for the presence
    tracking."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    # battle-room-categorie-v1: the room joins the PROVISIONED category
    # — the declare salon lives in the battlefield group, the attack
    # salon sits next to it.
    battlefield = await guild.create_category("Champs de Bataille")
    declare = await guild.create_text_channel("Déclarer une attaque", category=battlefield)
    assert declare is not None
    # restructuration-serveur-v1 b: the meeting message lives in the
    # ATTACK SALON — record it BEFORE the room opens.
    salon = await guild.create_text_channel("salon d'attaque", category=battlefield)
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    # The scheduler passes the FRESH store object (a stale pre-draft
    # snapshot would carry an empty pick & ban).
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    # The fixture declares with a legacy lobby URL — production declares
    # empty (the ID is shared at the RDV, v5): clear it like the real flow.
    attack.lobby_url = ""
    await bundle.store.upsert_attack(attack.to_mongo())
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room_id = fresh.score.get("room_channel_id")
    assert room_id
    assert fresh.score.get("room_role_id")
    room = guild.get_channel(int(room_id))
    assert room is not None
    assert room.user_limit == 2
    # battle-room-categorie-v1: the room sits in the PROVISIONED
    # category — and NO category was ever created (the legacy flow
    # created a « Champs de bataille » duplicate here).
    assert room.category is battlefield
    assert [c.name for c in guild.categories] == ["Champs de Bataille"]
    # Check 75 live: the @everyone deny locked the bot out of its own
    # room (403 50001 on the text-in-voice message) — the bot must carry
    # its own overwrites on the channel it creates.
    mine = room._overwrites.get(guild.me)
    assert mine is not None
    assert mine.view_channel is True
    assert mine.send_messages is True
    # The meeting single-message lands in the SALON — the voice room
    # keeps only the voice (b). battle-room-id-v2: the open also PINGS
    # the two players (message #2) so they cannot miss the scoreboard.
    assert room.messages == []
    assert len(salon.messages) == 2
    ping = str(salon.messages[1].content or "")
    assert "<@king-a>" in ping and "<@lord-b>" in ping
    body = salon.messages[0].content or ""
    # Drasah 18/10 v4: the meeting shows the FINAL civ of each player only
    # (post-sabotage, first surviving pick) — the full hands stay in DM.
    assert "francs" in body
    assert "huns" in body
    assert "bretons" not in body
    assert "perses" not in body
    # v5 single message: 🔗 ID only at first — ✅ Prêt appears once
    # the ID is set, 🏆 once both players are Prêt.
    view = salon.messages[0].view
    ids = {str(c.custom_id) for c in view.children}
    assert any(cid.startswith("kingdoms:attack:room:url:") for cid in ids)
    assert not any(cid.startswith("kingdoms:attack:room:ready:") for cid in ids)
    assert not any(cid.startswith("kingdoms:attack:room:result:") for cid in ids)
    assert fresh.score.get("meeting_channel_id") == salon.id
    assert fresh.score.get("meeting_message_id") == salon.messages[0].id
    # v4: the sabotage moved to the DMs — no sabotage button in the room.
    assert not any("sabotage" in cid or cid.startswith("kingdoms:attack:room:sab:") for cid in ids)
    # Idempotent: a replayed tick never duplicates the room.
    await _open_battle_room(bot, guild, fresh, bundle.kingdoms, "fr")
    rooms = [c for c in guild.channels if getattr(c, "user_limit", None) == 2]
    assert len(rooms) == 1
    # The ID is shared — the SAME message refreshes: ✅ Prêt unlocks,
    # the ID shows in the body, 🏆 stays hidden until both are Prêt.
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _refresh_room_message_safe

    await bundle.attacks.set_lobby_url(attack.id, "king-a", "4242")
    stamped = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _refresh_room_message_safe(bot, stamped, "fr")
    assert len(salon.messages) == 2  # the SAME salon message refreshes
    refreshed = {str(c.custom_id) for c in salon.messages[0].view.children}
    assert any(cid.startswith("kingdoms:attack:room:ready:") for cid in refreshed)
    assert not any(cid.startswith("kingdoms:attack:room:result:") for cid in refreshed)
    assert "4242" in str(salon.messages[0].content or "")


async def test_battle_room_never_creates_a_category() -> None:
    """battle-room-categorie-v1 (Drasah 19/10): the legacy room flow
    created a « Champs de bataille » category duplicate (its exact-name
    lookup never matched the provisioned « Champs de Bataille »). With
    no category at all the room lands at the guild root — NEVER a new
    category."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room = guild.get_channel(int(fresh.score["room_channel_id"]))
    assert room is not None
    # No category anywhere: the room sits at the guild root and the
    # flow NEVER created one.
    assert room.category is None
    assert guild.categories == []


async def test_battle_room_joins_the_category_of_the_declare_salon() -> None:
    """battle-room-categorie-v1: the room resolves the battlefield
    category through the declare salon's own category — the two live
    in the same provisioned group, whatever the category's name."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _battlefield_category

    guild = MockGuild()
    assert _battlefield_category(guild) is None
    battlefield = await guild.create_category("Champs de Bataille")
    await guild.create_text_channel("Déclarer une attaque", category=battlefield)
    assert _battlefield_category(guild) is battlefield
    # A lone category without any declare salon still matches by slug.
    guild2 = MockGuild()
    other = await guild2.create_category("Champs de Bataille")
    assert _battlefield_category(guild2) is other


async def test_battle_room_text_post_failure_still_grants_roles_and_dms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check 75: the text-in-voice post is best effort — a failing send
    must not bury the role grants, the room refs nor the room DMs."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room
    from tests.mocks.discord_mock import MockTextChannel

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)

    class _DmUser:
        def __init__(self, uid: str) -> None:
            self.id = uid

        async def send(self, content: str, view: Any = None) -> Any:
            outbox.append((str(self.id), content))
            return None

    class _DmBot(_FakeBot):
        def get_user(self, user_id: str) -> Any:
            return _DmUser(str(user_id))

    outbox: list[tuple[str, str]] = []
    bot = _DmBot(bundle)
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)

    original_send = MockTextChannel.send

    async def failing_send(self: MockTextChannel, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("403 50001 Missing Access (simulated)")

    monkeypatch.setattr(MockTextChannel, "send", failing_send)
    try:
        await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    finally:
        monkeypatch.setattr(MockTextChannel, "send", original_send)
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    # The refs are stored BEFORE the message: cleanup can find the room.
    assert fresh.score.get("room_channel_id")
    assert fresh.score.get("room_role_id")
    # Both players still got the room DM pointing at the vocal.
    dm_targets = {uid for uid, _msg in outbox}
    assert dm_targets >= {str(attack.attacker_lord_id), str(attack.defender_lord_id)}


async def test_room_ready_button_announces_the_match_when_both_ready() -> None:
    """Check 76: the vocal's ✅ Prêt button records the readiness and,
    once BOTH players clicked, creates the match scheduled event and
    announces the start in the room (a re-click never re-announces)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomReadyButton,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    # open_meeting: the score phase must be OPEN before a Prêt click.
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    # battle-room-id-v1: one Prêt button PER SIDE (attacker/defender).
    button = KingdomRoomReadyButton(
        attack.id, strings["room_ready_attacker_button"].format(player="king-a"), side="attacker"
    )
    other_side = KingdomRoomReadyButton(
        attack.id, strings["room_ready_defender_button"].format(player="lord-b"), side="defender"
    )

    # Drasah 18/10 v4: no lobby URL yet — the Prêt is politely refused.
    raw = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    raw.lobby_url = ""
    await bundle.store.upsert_attack(raw.to_mongo())
    refused = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr")
    # type: ignore[arg-type]
    await button.callback(refused)  # type: ignore[arg-type]
    assert refused.response.message is not None
    assert "ID" in str(refused.response.message.content or "")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert not (fresh.score or {}).get("ready")
    # The attacker shares the lobby URL — the gate opens.
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "aoe2de://lobby/42")

    # battle-room-id-v1: the WRONG side is refused cleanly (each Prêt
    # button belongs to ONE side of the scoreboard).
    wrong = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await button.callback(wrong)  # type: ignore[arg-type]
    assert wrong.response.message is not None
    # battle-room-id-v2: the refusal is worded for the player and
    # self-destructs after 10 s (timer armed, never blocks the test).
    assert "réservé à votre adversaire" in str(wrong.response.message.content or "")
    fresh_wrong = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert not (fresh_wrong.score or {}).get("ready")

    created: list[dict[str, Any]] = []

    async def _fake_create_scheduled_event(**kwargs: Any) -> Any:
        created.append(kwargs)

        class _E:
            id = 987654321

        return _E()

    guild.create_scheduled_event = _fake_create_scheduled_event  # type: ignore[method-assign]
    first = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await button.callback(first)  # type: ignore[arg-type]
    # battle-room-id-v1: NO ephemeral bubble — the interaction defers
    # silently and the SCOREBOARD (the salon's single message) shows
    # the Prêt on the attacker's side, the defender still waiting.
    assert first.response.deferred
    scoreboard = str(salon.messages[0].content or "")
    assert "✅ Prêt" in scoreboard
    assert "en attente du Prêt" in scoreboard
    assert not created
    second = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await other_side.callback(second)  # type: ignore[arg-type]
    # Both ready: the match event is created once, the ref is stored.
    assert len(created) == 1
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.score or {}).get("room_event_id") == 987654321
    # v5: no extra post — the SAME salon message carries 🏆 and the
    # « bonne game » footer once both are Prêt (no « La partie commence »).
    # v2: the salon counts TWO messages (scoreboard + player ping).
    assert len(salon.messages) == 2
    final_view = {str(c.custom_id) for c in salon.messages[0].view.children}
    assert any(cid.startswith("kingdoms:attack:room:result:") for cid in final_view)
    assert "bonne game" in str(salon.messages[0].content or "")
    assert not any("La partie commence" in (m.content or "") for m in salon.messages)
    # A re-click by the same player never re-announces the match.
    again = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    # type: ignore[arg-type]
    await other_side.callback(again)  # type: ignore[arg-type]
    assert len(created) == 1
    # v2: the 10 s self-destruction timer of the wrong-side refusal
    # never blocks the suite.
    import asyncio

    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_room_url_button_locks_the_entry_then_a_changed_id_resets_the_ready() -> None:
    """battle-room-id-v1: the 🔗 click instantly locks the shared view
    (« saisie de l'ID en cours », disabled) and opens the modal — and a
    CHANGED ID submitted through the modal resets both Prêts with the
    dedicated ephemeral note."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomUrlButton,
        KingdomRoomUrlModal,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    # An ID is already shared and both players are Prêt.
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "aoe2de://lobby/1")
    ready = await bundle.attacks.mark_ready(attack.id, "king-a")
    await bundle.attacks.mark_ready(ready.id, "lord-b")
    # The attacker asks for a modification — the shared view LOCKS.
    button = KingdomRoomUrlButton(attack.id, strings["room_id_modify_button"])
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(click)  # type: ignore[arg-type]
    assert click.response.modal is not None
    assert isinstance(click.response.modal, KingdomRoomUrlModal)
    locked = salon.messages[0].view.children
    url_buttons = [b for b in locked if str(b.custom_id or "").startswith("kingdoms:attack:room:url:")]
    assert len(url_buttons) == 1
    assert url_buttons[0].item.label == strings["room_id_pending_button"]  # type: ignore[attr-defined]
    assert url_buttons[0].item.disabled is True  # type: ignore[attr-defined]
    # A CHANGED ID through the modal resets both Prêts (ephemeral note).
    modal = click.response.modal
    modal.url_input._value = "aoe2de://lobby/2"  # type: ignore[attr-defined]
    submit = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await modal.on_submit(submit)  # type: ignore[arg-type]
    # battle-room-id-v2: the modal submit defers silently — the reset
    # of the Prêts reads directly on the scoreboard (no ephemeral note).
    assert submit.response.deferred
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.lobby_url == "aoe2de://lobby/2"
    assert (fresh.score or {}).get("ready") == []
    assert "all_ready" not in (fresh.score or {})
    scoreboard = str(salon.messages[0].content or "")
    assert "aoe2de://lobby/2" in scoreboard
    # The pending unlock task (grace timer) never blocks the suite.
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_sabotage_phase_service_both_sides_and_resolution() -> None:
    """Drasah 18/10 v4 (phase Sabotage DM, après le draft): BOTH sides
    with the bought techno may remove up to 2 enemy civs while the
    meeting is DEFENDED and T is not reached; the phase resolves when
    both camps settle — an AI defender is settled from the start."""
    import pytest as _pytest

    from kingdoms.mods.kingdoms.attacks import AttackStateError, PickBanStateError

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    # meeting(): the draft is resolved, T = 18:00, now is frozen at
    # 17:50 — the sabotage phase is OPEN.
    attack, aquitaine, bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=1)
    await bundle.stock(bourgogne, sabotage=1)

    # The attacker snipes the defender's first-ranked civ.
    moved = await bundle.attacks.sabotage_techno(attack.id, "king-a", "huns")
    assert (moved.pickban or {}).get("attacker_sabotages") == ["huns"]
    # The DEFENDER may sabotage too (v4 — attacker-only rule gone).
    moved_b = await bundle.attacks.sabotage_techno(attack.id, "lord-b", "francs")
    assert (moved_b.pickban or {}).get("defender_sabotages") == ["francs"]
    # A double snipe or an outsider civ is refused.
    with _pytest.raises(AttackStateError):
        await bundle.attacks.sabotage_techno(attack.id, "king-a", "huns")
    with _pytest.raises(AttackStateError):
        await bundle.attacks.sabotage_techno(attack.id, "king-a", "francs")
    # No techno, no sabotage (the stock of Bourgogne is consumed? no —
    # but a kingdom without the purchase is refused).
    # Settling one side alone resolves nothing.
    first = await bundle.attacks.sabotage_settle(attack.id, "king-a")
    assert not (first.pickban or {}).get("sabotage_resolved")
    assert (first.pickban or {}).get("sabotage_settled_attacker") is True
    # The second settle resolves the phase exactly once.
    second = await bundle.attacks.sabotage_settle(attack.id, "lord-b")
    assert (second.pickban or {}).get("sabotage_resolved") is True
    again = await bundle.attacks.sabotage_settle(attack.id, "lord-b")
    assert (again.pickban or {}).get("sabotage_resolved") is True
    # After the resolution the window is closed.
    with _pytest.raises(PickBanStateError):
        await bundle.attacks.sabotage_techno(attack.id, "king-a", "perses")
    # A third party may never sabotage.
    with _pytest.raises(PickBanStateError):
        await bundle.attacks.sabotage_techno(attack.id, "stranger", "perses")


async def test_sabotage_techno_consumes_the_bought_charge() -> None:
    """sabotage-consommable-v1 (Drasah 22/10): one bought Sabotage
    charge is CONSUMED at the snipe — the remaining stock decrements,
    a refused pick (bad civ, cap reached, empty stock) never consumes
    a charge, and the 2-per-attack cap stays independent of the
    bought stock."""
    from kingdoms.mods.kingdoms.attacks import (
        AttackStateError,
        SabotageError,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=3)

    # First snipe: the charge is spent (3 bought -> 2 left).
    moved = await bundle.attacks.sabotage_techno(attack.id, "king-a", "huns")
    assert (moved.pickban or {}).get("attacker_sabotages") == ["huns"]
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases.get("sabotage") == 2

    # A REFUSED pick never consumes a charge (wrong civ).
    with pytest.raises(AttackStateError):
        await bundle.attacks.sabotage_techno(attack.id, "king-a", "francs")
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases.get("sabotage") == 2

    # Second snipe -> 1 left; the third hits the 2-per-attack CAP
    # before touching the stock.
    await bundle.attacks.sabotage_techno(attack.id, "king-a", "perses")
    with pytest.raises(SabotageError):
        await bundle.attacks.sabotage_techno(attack.id, "king-a", "byzantins")
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases.get("sabotage") == 1

    # An empty stock refuses the snipe even with civs available
    # (the defender side — its per-attack cap is still fresh).
    await bundle.stock(_bourgogne, sabotage=1)
    await bundle.attacks.sabotage_techno(attack.id, "lord-b", "francs")
    with pytest.raises(AttackStateError):
        await bundle.attacks.sabotage_techno(attack.id, "lord-b", "bretons")
    state = await bundle.attacks.technology_state(_bourgogne)
    assert state.purchases.get("sabotage") == 0


async def test_sabotage_dm_buttons_flow_and_no_resources_notice() -> None:
    """The 🗡️ DM button opens the ephemeral menu (no techno → the
    sab_no_resources notice); the techno path removes a civ and the
    ⚔️ Passer buttons of both sides close the phase."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=1)
    _wiring(bundle)
    guild = MockGuild()
    strings = _strings("fr")

    open_btn = KingdomSabotageOpenButton(attack.id, "king-a", strings["sab_open_button"])
    assert len(str(open_btn.custom_id)) <= 100

    # The DEFENDER has no techno — his button shows the notice.
    defender_btn = KingdomSabotageOpenButton(attack.id, "lord-b", strings["sab_open_button"])
    click_b = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=None, locale="fr")
    # type: ignore[arg-type]
    await defender_btn.callback(click_b)  # type: ignore[arg-type]
    assert click_b.response.message is not None
    assert "sabotage" in str(click_b.response.message.content or "").lower()

    # The ATTACKER opens the ephemeral menu — techno only (no volière).
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    # type: ignore[arg-type]
    await open_btn.callback(click)  # type: ignore[arg-type]
    assert click.response.message is not None
    menu = click.response.message.view
    assert menu is not None
    labels = [str(getattr(child, "label", "")) for child in menu.children]
    assert strings["sab_techno"] in labels
    assert strings["sab_pass_button"] not in labels  # Passer stays in the DM

    # The techno path: pick one enemy civ — it is removed.
    techno = next(c for c in menu.children if str(getattr(c, "label", "")) == strings["sab_techno"])
    step = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")  # type: ignore[arg-type]
    await techno.callback(step)  # type: ignore[arg-type]
    civ_view = step.response.message.view if step.response.message is not None else None
    assert civ_view is not None
    select = next(c for c in civ_view.children if hasattr(c, "callback"))
    select._values = ["huns"]  # type: ignore[attr-defined]
    pick = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")  # type: ignore[arg-type]
    await select.callback(pick)  # type: ignore[arg-type]
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.pickban or {}).get("attacker_sabotages") == ["huns"]

    # Both Passer clicks close the phase; the second answers phase_done.
    passer_a = KingdomSabotagePassButton(attack.id, "king-a", strings["sab_pass_button"])
    first = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    # type: ignore[arg-type]
    await passer_a.callback(first)  # type: ignore[arg-type]
    assert "attente" in str(first.response.message.content or "")
    passer_b = KingdomSabotagePassButton(attack.id, "lord-b", strings["sab_pass_button"])
    second = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=None, locale="fr")
    # type: ignore[arg-type]
    await passer_b.callback(second)  # type: ignore[arg-type]
    resolved = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (resolved.pickban or {}).get("sabotage_resolved") is True
    # A late open is politely refused (phase over).
    late = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")  # type: ignore[arg-type]
    await open_btn.callback(late)  # type: ignore[arg-type]
    assert "termin" in str(late.response.message.content or "").lower()


async def test_cleanup_battle_rooms_waits_the_delay_after_the_resolution() -> None:
    """Drasah 18/10 v4 (live incident): the vocal must NEVER disappear
    mid-game — the room is deleted 30 minutes AFTER a winner is known."""
    from datetime import UTC, datetime, timedelta

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _cleanup_battle_rooms_safe,
        _open_battle_room,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    # restructuration-serveur-v1 b: the attack's OWN salon follows the
    # voice room into the same cleanup window.
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room_id = int(fresh.score["room_channel_id"])
    assert guild.get_channel(room_id) is not None
    assert guild.get_channel(salon.id) is not None

    # A winner is declared — the resolution timestamp is FRESH (the
    # attacks clock is frozen at 2026-10-11, the cleanup tick reads
    # the real clock, so pin the resolution on the real now).
    # The cleanup tick reads bot.guilds, not the passed guild — point
    # the fake bot at the real MockGuild holding the room.
    bot.guilds = [guild]
    await bundle.attacks.resolve(attack.id, aquitaine)
    resolved = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    resolved.resolved_at = datetime.now(UTC)
    await bundle.store.upsert_attack(resolved.to_mongo())
    assert [c for c in guild.channels if getattr(c, "user_limit", None) == 2]
    # The tick right after the resolution keeps the room alive.
    kept = await _cleanup_battle_rooms_safe(bot, guild, bundle.attacks)
    assert kept == 0
    assert guild.get_channel(room_id) is not None
    assert guild.get_channel(salon.id) is not None
    # Simulate the 30-minute grace elapsing: push the resolution back.
    resolved = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    resolved.resolved_at = datetime.now(UTC) - timedelta(minutes=31)
    await bundle.store.upsert_attack(resolved.to_mongo())
    removed = await _cleanup_battle_rooms_safe(bot, guild, bundle.attacks)
    assert removed == 2  # the voice room AND the attack salon
    assert guild.get_channel(room_id) is None
    assert guild.get_channel(salon.id) is None
    cleaned = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (cleaned.score or {}).get("room_cleaned")
    assert (cleaned.score or {}).get("attack_salon_cleaned")


async def test_sweep_deletes_leftovers_of_a_flagged_clean_room() -> None:
    """battle-role-sweep-v1 (Drasah 20/10, live lockout incident): the
    cleanup flags the room ``room_cleaned`` even when Discord REFUSED
    the delete — the leftover channel/role is flagged clean and never
    retried. The sweep re-deletes by ref, flag or not."""
    from datetime import UTC, datetime, timedelta

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _cleanup_battle_rooms_safe,
        _open_battle_room,
    )
    from tests.mocks.discord_mock import MockRole

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    room_id = int(fresh.score["room_channel_id"])
    role_id = int(fresh.score["room_role_id"])
    bot.guilds = [guild]

    # The lockout simulation: the flags say cleaned, Discord refused.
    await bundle.attacks.resolve(attack.id, aquitaine)
    resolved = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    resolved.resolved_at = datetime.now(UTC) - timedelta(minutes=31)
    await bundle.store.upsert_attack(resolved.to_mongo())
    await bundle.attacks.mark_room_cleaned(attack.id)
    await bundle.attacks.mark_attack_salon_cleaned(attack.id)
    # The room role never disappears on its own in the mock: bind the
    # in-memory delete like the real API would.
    role = guild.get_role(role_id)
    assert isinstance(role, MockRole)

    def _bind_delete(target: MockRole) -> None:
        async def _delete(*, reason: str | None = None) -> None:
            guild._roles.pop(target.id, None)

        target.delete = _delete  # type: ignore[method-assign]

    if role is not None:
        _bind_delete(role)

    # The main pass skips everything (flags set) — the sweep deletes.
    played = await _cleanup_battle_rooms_safe(bot, guild, bundle.attacks)
    assert played == 0  # the flagged pass found nothing to do
    assert guild.get_channel(room_id) is None
    assert guild.get_channel(salon.id) is None
    assert guild.get_role(role_id) is None
    # Idempotent: a second sweep on the clean guild touches nothing.
    assert await _cleanup_battle_rooms_safe(bot, guild, bundle.attacks) == 0


async def test_sweep_keeps_a_live_room_and_deletes_orphan_battle_roles() -> None:
    """battle-role-sweep-v1: a name-matching battle role claimed by NO
    attack (a record failure at the room open) is swept; the role and
    room of a LIVE meeting survive the sweep untouched."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _cleanup_battle_rooms_safe,
        _open_battle_room,
    )
    from tests.mocks.discord_mock import MockRole

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    bot = _FakeBot(bundle)
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    live_role_id = int(fresh.score["room_role_id"])
    live_room_id = int(fresh.score["room_channel_id"])
    bot.guilds = [guild]

    # An orphan: the « Bataille … » naming pattern, claimed by nothing.
    orphan = await guild.create_role(name="Bataille orpheline")
    decoy = await guild.create_role(name="Royaumes Lord")
    for target in (orphan, decoy, guild.get_role(live_role_id)):
        assert isinstance(target, MockRole)

        def _bind_delete(t: MockRole) -> None:
            async def _delete(*, reason: str | None = None) -> None:
                guild._roles.pop(t.id, None)

            t.delete = _delete  # type: ignore[method-assign]

        _bind_delete(target)

    played = await _cleanup_battle_rooms_safe(bot, guild, bundle.attacks)
    assert played == 0  # the live meeting keeps everything
    assert guild.get_channel(live_room_id) is not None
    assert guild.get_role(live_role_id) is not None
    assert guild.get_role(orphan.id) is None  # swept
    assert guild.get_role(decoy.id) is not None  # never a battle role


async def test_result_button_needs_both_ready_and_posts_the_conquest_recap() -> None:
    """battle-room-id-v2: 🏆 refuses before both Prêt; the winner flow
    is TWO-STAGE (select → EXPLICIT confirmation, anti-missclick) and
    the result applies only with the OTHER player's confirm (review
    view — never a second declaration). The resolved match posts the
    Géopolitique conquest recap with its detail thread (ID reused)."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomResultButton,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    geo = MockTextChannel(name="Géopolitique", guild=guild)
    guild._channels[geo.id] = geo
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "4242")
    button = KingdomRoomResultButton(attack.id, strings["result_button"])
    # Not both Prêt yet — the stale 🏆 click is politely refused.
    early = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr")
    await button.callback(early)  # type: ignore[arg-type]
    assert early.response.message is not None
    assert "Prêts" in str(early.response.message.content or "")
    # Both Prêt — the attacker starts the two-stage declaration.
    await bundle.attacks.mark_ready(attack.id, "king-a")
    await bundle.attacks.mark_ready(attack.id, "lord-b")
    click_a = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(click_a)  # type: ignore[arg-type]
    # Stage 1: the ephemeral winner select.
    assert click_a.response.message is not None
    select = click_a.response.message.view.children[0]  # type: ignore[union-attr]
    select._values = [str(aquitaine)]  # type: ignore[attr-defined]
    pick_a = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await select.callback(pick_a)  # type: ignore[arg-type,union-attr,func-returns-value]
    # Stage 2: the explicit confirmation (anti-missclick — the ✏️
    # Modifier loops back to the select, never a silent declaration).
    assert pick_a.response.message is not None
    assert "confirmer" in str(pick_a.response.message.content or "").lower()
    labels = [str(c.label) for c in pick_a.response.message.view.children]  # type: ignore[union-attr]
    assert labels == [strings["result_confirm_button"], strings["result_edit_button"]]
    # The ✏️ Modifier loops back to the winner select via an edit.
    change = pick_a.response.message.view.children[1]  # type: ignore[union-attr]
    redo = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await change.callback(redo)  # type: ignore[arg-type,func-returns-value]
    assert "Qui remporte" in str(redo.response.message.content or "")
    # The attacker settles on Aquitaine again and CONFIRMS.
    select2 = redo.response.message.view.children[0]  # type: ignore[union-attr]
    select2._values = [str(aquitaine)]  # type: ignore[attr-defined]
    pick_a2 = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await select2.callback(pick_a2)  # type: ignore[arg-type,union-attr,func-returns-value]
    confirm = pick_a2.response.message.view.children[0]  # type: ignore[union-attr]
    okay = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await confirm.callback(okay)  # type: ignore[arg-type,func-returns-value]
    assert okay.response.message is not None
    assert "Résultat enregistré" in str(okay.response.message.content or "")
    # PENDING until the other side confirms — the attack is NOT over.
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert not fresh.is_over
    # The defender can no longer DECLARE a winner — the review view
    # only offers ✅ Confirmer le résultat / ⚖️ Contester.
    click_b = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(click_b)  # type: ignore[arg-type]
    assert click_b.response.message is not None
    assert "a déclaré" in str(click_b.response.message.content or "")
    review = click_b.response.message.view.children  # type: ignore[union-attr]
    review_labels = [str(c.label) for c in review]
    assert review_labels == [strings["result_review_confirm_button"], strings["result_review_contest_button"]]
    # The defender CONFIRMS the same winner — the result applies now.
    confirm_b = review[0]
    okay_b = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    await confirm_b.callback(okay_b)  # type: ignore[arg-type,func-returns-value]
    assert okay_b.response.message is not None
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert fresh.is_over
    assert str(fresh.winner_kingdom_id) == str(aquitaine)
    # The conquest recap landed in Géopolitique with its detail thread.
    assert geo.messages, "the conquest recap landed in Géopolitique"
    assert "a conquis" in str(geo.messages[0].content or "")
    assert geo.threads, "the recap spawned its detail thread"
    assert geo.threads[0].name == "Détail de la conquête"
    detail = str(geo.threads[0].messages[0].content or "")
    assert "4242" in detail  # the game ID declared earlier is reused
    assert "jouait" in detail  # the played civilizations
    # v2: the armed self-destruction timers never block the suite.
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_result_contest_opens_a_dispute_kept_until_the_admins_arbitrate() -> None:
    """battle-room-id-v2: a CONTESTED review (contrary declaration)
    opens the dispute — the ephemeral reminder STAYS (no timer, the
    admins arbitrate) and mentions taking contact with an admin."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomRoomResultButton,
        _open_battle_room,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    strings = _strings("fr")
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "4242")
    await bundle.attacks.mark_ready(attack.id, "king-a")
    await bundle.attacks.mark_ready(attack.id, "lord-b")
    button = KingdomRoomResultButton(attack.id, strings["result_button"])
    # The attacker declares Aquitaine — select then confirm.
    click_a = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(click_a)  # type: ignore[arg-type]
    select = click_a.response.message.view.children[0]  # type: ignore[union-attr]
    select._values = [str(aquitaine)]  # type: ignore[attr-defined]
    pick_a = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await select.callback(pick_a)  # type: ignore[arg-type,union-attr,func-returns-value]
    confirm = pick_a.response.message.view.children[0]  # type: ignore[union-attr]
    okay = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await confirm.callback(okay)  # type: ignore[arg-type,func-returns-value]
    # The defender contests — the contrary declaration opens a dispute.
    click_b = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(click_b)  # type: ignore[arg-type]
    contest = click_b.response.message.view.children[1]  # type: ignore[union-attr]
    assert str(contest.label) == strings["result_review_contest_button"]
    contest_b = MockInteraction(user=MockUser(id="lord-b"), guild=guild, channel=room, locale="fr", client=bot)
    await contest.callback(contest_b)  # type: ignore[arg-type,func-returns-value]
    assert contest_b.response.message is not None
    content = str(contest_b.response.message.content or "")
    assert "contradictoire" in content
    assert "admin" in content.lower()
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.score or {}).get("disputed")
    assert not fresh.is_over
    # The dispute freezes the flow: a new 🏆 click shows the SAME
    # dispute reminder (staying message), never a fresh select.
    later = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=room, locale="fr", client=bot)
    await button.callback(later)  # type: ignore[arg-type]
    assert "contradictoire" in str(later.response.message.content or "")
    assert later.response.message.view is None or not later.response.message.view.children
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_room_row_order_and_duel_names() -> None:
    """battle-room-id-v2: once both players are Prêt the single salon
    message carries FOUR components in the Drasah order — Prêt
    attaquant | 🔗 ID | 🏆 Déclarer le vainqueur | Prêt défenseur —
    and the duel names (🗡️ salon, ⚔️ vocal) are slug-based with a
    numbered suffix on collision, never the legacy prose names."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _open_battle_room,
        _room_channel_name,
        _strings,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    strings = _strings("fr")
    await bundle.attacks.set_lobby_url(attack.id, "king-a", "4242")
    await bundle.attacks.mark_ready(attack.id, "king-a")
    await bundle.attacks.mark_ready(attack.id, "lord-b")
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _refresh_room_message_safe

    await _refresh_room_message_safe(bot, fresh, "fr")
    ids = [str(c.custom_id) for c in salon.messages[0].view.children]
    assert len(ids) == 4
    assert ids[0].startswith("kingdoms:attack:room:ready:") and ids[0].endswith(":attacker")
    assert ids[1].startswith("kingdoms:attack:room:url:")
    assert ids[2].startswith("kingdoms:attack:room:result:")
    assert ids[3].startswith("kingdoms:attack:room:ready:") and ids[3].endswith(":defender")
    labels = [str(c.item.label) for c in salon.messages[0].view.children]  # type: ignore[attr-defined]
    assert labels[0] == strings["room_ready_attacker_button"].format(player="King A")
    assert labels[3] == strings["room_ready_defender_button"].format(player="Lord B")
    # The duel names: the VOCAL room wears the ⚔️ prefix (a defender
    # is present), the TEXT salon the 🗡️ prefix — slug, -vs- separator,
    # and a numbered suffix separates SIMULTANEOUS duels on the names.
    room = next(c for c in guild.channels if getattr(c, "user_limit", None) == 2)
    assert room.name == "⚔️-king-a-vs-lord-b"
    twin = _room_channel_name(guild, "🗡️", "King A", "Lord B")
    assert twin == "🗡️-king-a-vs-lord-b"
    # The twin salon really exists now — the NEXT duel steps to -2.
    await guild.create_text_channel(twin)
    again = _room_channel_name(guild, "🗡️", "king-a", "lord-b")
    assert again == "🗡️-king-a-vs-lord-b-2"


async def test_room_open_pings_both_players_but_never_the_ai() -> None:
    """battle-room-id-v2: the salon PINGS the two players when the
    room opens — against the AI defense only the ATTACKER is pinged
    (no ping-able user on the other side), the scoreboard shows 🤖 IA."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    # The AI took the defense — the ping targets the attacker alone.
    attack.ai_defender = True
    await bundle.store.upsert_attack(attack.to_mongo())
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    pings = [m for m in salon.messages if "à vos armes" in str(m.content or "")]
    assert len(pings) == 1
    assert "<@king-a>" in str(pings[0].content or "")
    assert "<@lord-b>" not in str(pings[0].content or "")
    scoreboard = str(salon.messages[0].content or "")
    assert "🤖 IA" in scoreboard
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_ai_room_shows_the_raw_level_and_the_disclaimer() -> None:
    """battle-ai-rdv-v1: vs the bot AI the scoreboard carries the RAW
    level (« 🤖 IA — niv. N/5 », Traquenard-aware) and the short
    multiplayer-ID disclaimer sits right under it."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _open_battle_room

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    attack.ai_defender = True
    effects = dict(attack.effects or {})
    effects["ai_level"] = 4
    attack.effects = effects
    await bundle.store.upsert_attack(attack.to_mongo())
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")
    scoreboard = str(salon.messages[0].content or "")
    assert "🤖 IA — niv. 4/5" in scoreboard
    assert "multijoueur" in scoreboard
    assert "fraude" in scoreboard


async def test_declared_note_timer_deletes_the_note_at_the_meeting_time() -> None:
    """battle-ai-rdv-v1: the declared/itinerary ephemeral self-destructs
    at the MEETING time (token life 15 min — a later meeting keeps it)."""
    import asyncio
    from datetime import timedelta

    from kingdoms.mods.kingdoms.game_clock import game_now
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _DECLARE_NOTE_TASKS,
        _arm_declared_note_timer,
    )

    interaction = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        locale="fr",  # type: ignore[arg-type]
    )
    note = await interaction.followup.send("⚔️ Attaque déclarée", ephemeral=True)
    # A far meeting (> 15 min of followup-token life) — never armed.
    _arm_declared_note_timer("attack-far", interaction, note.id, game_now() + timedelta(minutes=30))
    assert "attack-far" not in _DECLARE_NOTE_TASKS
    # A near meeting — the timer deletes the note at the deadline.
    _arm_declared_note_timer("attack-x", interaction, note.id, game_now() + timedelta(seconds=0.05))
    assert "attack-x" in _DECLARE_NOTE_TASKS
    await asyncio.sleep(0.15)
    assert not [m for m in interaction.followup.messages if m.id == note.id]
    assert "attack-x" not in _DECLARE_NOTE_TASKS


async def test_sabotage_stock_line_counts_and_faucon_state() -> None:
    """sabotage-ux-v1: the draft & ban DM carries the stock line —
    the Sabotage techno count (never consumed by a use) and the Faucon
    state (ready / flying / absent)."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import _sabotage_stock_line, _strings
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _get_wiring

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=2)
    _wiring(bundle)
    wiring = _get_wiring()
    strings = _strings("fr")

    # No faucon slot — the line says absent, the sabotage count shows.
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    line = await _sabotage_stock_line(wiring, attack, "attacker", strings)
    assert "🗡️ Sabotage : 2" in line
    assert "🦅 Faucon : absent" in line
    assert "2 civilisations sabotées maximum" in line

    # A faucon landed (cooldown in the past) — it is ready.
    kingdom.voliere_slots = ["faucon"]
    kingdom.voliere_cooldowns = {"faucon": 0}
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    line = await _sabotage_stock_line(wiring, attack, "attacker", strings)
    assert "🦅 Faucon : dispo" in line

    # The faucon is flying (cooldown far in the future) — the line
    # shows the Paris landing time.
    kingdom.voliere_cooldowns = {"faucon": 1900000000}
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    line = await _sabotage_stock_line(wiring, attack, "attacker", strings)
    assert "en vol jusqu'à" in line

    # A kingdom without the purchase shows the zero count.
    line = await _sabotage_stock_line(wiring, attack, "defender", strings)
    assert "🗡️ Sabotage : 0" in line
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_sabotage_panel_edits_in_place_and_dies_at_passer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sabotage-ux-v1: the sabotage phase lives on ONE ephemeral panel
    per player — the menu click and the civ pick EDIT it in place (no
    stacking bubbles), the panel carries the X/2 récap + stock line,
    and the ⚔️ Passer click deletes it at once.

    sabotage-visuel-v1 (Drasah 29/10, PATCH 1.0 item 2): the panel
    carries the enemy-picks MOSAIC, re-rendered at EVERY action with
    the sabotaged civs crossed — the pick attaches the recomposed file
    through the edit's ``attachments``."""
    import asyncio

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _SABOTAGE_PANELS,
        KingdomSabotageOpenButton,
        KingdomSabotagePassButton,
        _strings,
    )

    mosaic_calls: list[set[str]] = []

    class _FakeMosaicFile:
        pass

    async def fake_mosaic(
        entries: object,
        filename: str = "alliances.png",
        *,
        with_origin_badges: bool = True,
        crossed: object = None,
    ) -> object:
        mosaic_calls.append({str(civ) for civ in (crossed or ())})
        return _FakeMosaicFile()

    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=2)
    _wiring(bundle)
    guild = MockGuild()
    strings = _strings("fr")

    # The attacker opens the PANEL (title + stock line, one message).
    open_btn = KingdomSabotageOpenButton(attack.id, "king-a", strings["sab_open_button"])
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await open_btn.callback(click)  # type: ignore[arg-type]
    assert click.response.message is not None
    panel_content = str(click.response.message.content or "")
    assert "0/2" in panel_content
    assert "🗡️ Sabotage : 2" in panel_content
    key = (str(attack.id), "king-a")
    assert key in _SABOTAGE_PANELS
    assert _SABOTAGE_PANELS[key]["interaction"] is click
    # sabotage-visuel-v1: the opening panel already wears the mosaic.
    assert mosaic_calls == [set()]
    assert click.response.message.attachments and click.response.message.attachments[0].__class__ is _FakeMosaicFile

    # The techno click EDITS the panel — no new bubble, no followup.
    menu = click.response.message.view
    assert menu is not None
    techno = next(c for c in menu.children if str(getattr(c, "label", "")) == strings["sab_techno"])
    step = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await techno.callback(step)  # type: ignore[arg-type]
    assert step.followup.messages == []
    step_content = str(step.response.message.content or "") if step.response.message else ""
    assert "sab_title" not in step_content
    assert "0/2" in step_content  # the panel header survives the edit
    assert "Choisis la civilisation" in step_content
    assert step.response.message.attachments and step.response.message.attachments[0].__class__ is _FakeMosaicFile

    # The civ pick edits the SAME panel again — récap 1/2 + done note.
    civ_view = step.response.message.view if step.response.message else None
    assert civ_view is not None
    select = next(c for c in civ_view.children if hasattr(c, "callback"))
    select._values = ["huns"]  # type: ignore[attr-defined]
    pick = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await select.callback(pick)  # type: ignore[arg-type]
    assert pick.followup.messages == []
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.pickban or {}).get("attacker_sabotages") == ["huns"]
    pick_content = str(pick.response.message.content or "") if pick.response.message else ""
    assert "1/2" in pick_content
    assert "huns" in pick_content
    # sabotage-consommable-v1: the stock line decrements after the snipe
    # (2 bought charges - 1 used = 1 left, the panel is edited in place).
    assert "🗡️ Sabotage : 1" in pick_content
    # sabotage-visuel-v1: the pick re-rendered the mosaic with huns ❌.
    assert mosaic_calls[-1] == {"huns"}
    assert pick.response.message.attachments and pick.response.message.attachments[0].__class__ is _FakeMosaicFile

    # The ⚔️ Passer click deletes the panel at once.
    passer = KingdomSabotagePassButton(attack.id, "king-a", strings["sab_pass_button"])
    settle = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await passer.callback(settle)  # type: ignore[arg-type]
    assert click.original_deleted is True
    assert key not in _SABOTAGE_PANELS
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_sabotage_panel_fallback_resends_the_whole_panel_through_the_unique_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sabotage-visuel-v1 (Drasah 29/10, PATCH 1.0 item 2): when the
    in-place edit DIES (expired token, restarted panel), the fallback
    re-sends the WHOLE panel — récap + note + mosaic, NEVER a lone
    note bubble — and the window rides the fenêtre unique registry so
    the next fallback closes the previous one (no stacking)."""
    import asyncio

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _SABOTAGE_PANELS,
        KingdomSabotageOpenButton,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS

    class _FakeMosaicFile:
        pass

    async def fake_mosaic(
        entries: object,
        filename: str = "alliances.png",
        *,
        with_origin_badges: bool = True,
        crossed: object = None,
    ) -> object:
        return _FakeMosaicFile()

    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)
    _EPHEMERAL_WINDOWS.pop("king-a", None)

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=2)
    _wiring(bundle)
    guild = MockGuild()
    strings = _strings("fr")

    open_btn = KingdomSabotageOpenButton(attack.id, "king-a", strings["sab_open_button"])
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await open_btn.callback(click)  # type: ignore[arg-type]
    menu = click.response.message.view
    techno = next(c for c in menu.children if str(getattr(c, "label", "")) == strings["sab_techno"])
    step = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    await techno.callback(step)  # type: ignore[arg-type]
    civ_view = step.response.message.view
    select = next(c for c in civ_view.children if hasattr(c, "callback"))

    async def _raising_edit(**kwargs: object) -> None:
        raise RuntimeError("the token died")

    # The pick's in-place edit dies — the fallback carries récap + note
    # + mosaic in ONE followup, registered as the player's unique window.
    select._values = ["huns"]  # type: ignore[attr-defined]
    pick = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    monkeypatch.setattr(pick.response, "edit_message", _raising_edit)
    await select.callback(pick)  # type: ignore[arg-type]
    assert len(pick.followup.messages) == 1
    window = pick.followup.messages[0]
    fallback_content = str(window.content or "")
    assert "1/2" in fallback_content  # the récap rides the fallback
    assert "huns" in fallback_content
    assert "retirée du camp adverse" in fallback_content  # …and the note
    assert window.files and window.files[0].__class__ is _FakeMosaicFile
    stored = _EPHEMERAL_WINDOWS.get("king-a")
    assert stored is not None and stored[0] is pick and stored[2] is window

    # A SECOND dying pick replaces the first fallback — the stale
    # window is deleted (fenêtre unique), never stacked.
    select._values = ["perses"]  # type: ignore[attr-defined]
    pick2 = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    monkeypatch.setattr(pick2.response, "edit_message", _raising_edit)
    await select.callback(pick2)  # type: ignore[arg-type]
    assert window.deleted is True
    assert len(pick2.followup.messages) == 1
    stored2 = _EPHEMERAL_WINDOWS.get("king-a")
    assert stored2 is not None and stored2[2] is pick2.followup.messages[0]
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert sorted((fresh.pickban or {}).get("attacker_sabotages") or []) == ["huns", "perses"]

    _EPHEMERAL_WINDOWS.pop("king-a", None)
    _SABOTAGE_PANELS.pop((str(attack.id), "king-a"), None)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_pickban_validate_closes_the_stale_refusal_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """PATCH 1.0 item 1 (Drasah 29/10): the draft refusals ride the
    fenêtre unique registry — the doublon warning is DELETED when the
    player fixes his picks and validates (his 18h19 live report: the
    stale warning stayed stacked under the validated DM)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import KingdomPickBanView
    from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS

    _EPHEMERAL_WINDOWS.pop("king-a", None)
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    await bundle.attacks.reveal_due(now=datetime(2026, 10, 11, 17, 45, tzinfo=UTC))
    await bundle.attacks.pick_ban_due(now=datetime(2026, 10, 11, 17, 55, tzinfo=UTC))
    await bundle.give_civs("Aquitaine", "francs", "bretons", "goths")
    _wiring(bundle)

    view = KingdomPickBanView(str(attack.id), "king-a", "attacker", ["francs", "bretons", "goths"], "fr")

    # A duplicate → the refusal IS the player's unique window.
    for select, vals in zip(view.rank_selects, [["francs"], ["francs"], []]):
        select.values = vals  # type: ignore[misc]
    refused = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await view._on_validate(refused)  # type: ignore[arg-type]
    assert refused.response.ephemeral is True
    assert "doublons" in str(refused.response.message.content or "")
    stored = _EPHEMERAL_WINDOWS.get("king-a")
    assert stored is not None and stored[0] is refused

    # The corrected validation succeeds — and DELETES the stale warning.
    for select, vals in zip(view.rank_selects, [["francs"], ["bretons"], ["goths"]]):
        select.values = vals  # type: ignore[misc]
    validated = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await view._on_validate(validated)  # type: ignore[arg-type]
    assert refused.original_deleted is True  # the doublon warning is gone
    assert "king-a" not in _EPHEMERAL_WINDOWS
    assert "en attente de l'adversaire" in str(validated.response.message.content or "")

    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    assert (fresh.pickban or {}).get("attacker_picks") == ["francs", "bretons", "goths"]


async def test_sabotage_panel_timer_sweeps_the_closed_panel() -> None:
    """sabotage-ux-v1: the panel timer deletes the panel when it fires
    (the 15-minute token limit, or a short sweep when no action left)."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _SABOTAGE_PANELS,
        _arm_sabotage_panel_timer,
    )

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, _aquitaine, _bourgogne = await bundle.meeting()
    _wiring(bundle)
    guild = MockGuild()
    click = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=None, locale="fr")
    key = (str(attack.id), "king-a")
    _SABOTAGE_PANELS[key] = {"interaction": click}
    _arm_sabotage_panel_timer(key, seconds=0)
    await asyncio.sleep(0.05)
    assert click.original_deleted is True
    assert key not in _SABOTAGE_PANELS
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_sabotage_open_button_without_any_resource_answers_no_resources() -> None:
    """sabotage-garde-v1 (Drasah 28/10): a lord owning NEITHER a bought
    Sabotage NOR a usable Faucon clicks 🗡️ Saboter — the click answers
    the sab_no_resources ephemeral AT ONCE (before the guard, the Faucon
    button showed anyway whenever the volière was wired and the civ
    pick then died on a raw English error)."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _SABOTAGE_PANELS,
        KingdomSabotageOpenButton,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import (
        _EPHEMERAL_WINDOWS,
        KingdomsPanelWiring,
        register_kingdoms_panel_wiring,
    )

    _EPHEMERAL_WINDOWS.pop("king-a", None)
    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    # The volière service IS wired (the old bug lived here) but the
    # kingdom owns NO faucon slot and bought NO sabotage.
    register_kingdoms_panel_wiring(
        KingdomsPanelWiring(
            kingdoms_service=bundle.kingdoms,
            attacks_service=bundle.attacks,
            territories_service=bundle.territories,
            voliere_service=object(),
        )
    )
    strings = _strings("fr")
    open_btn = KingdomSabotageOpenButton(str(attack.id), "king-a", strings["sab_open_button"])
    click = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await open_btn.callback(click)  # type: ignore[arg-type]
    assert click.response.ephemeral is True
    content = str(click.response.message.content or "") if click.response.message else ""
    assert "ne disposez pas de sabotage ni de Faucon" in content
    assert (str(attack.id), "king-a") not in _SABOTAGE_PANELS
    # A faucon slot in cooldown is just as unavailable as no slot.
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.id == aquitaine)
    kingdom.voliere_slots = ["faucon"]
    kingdom.voliere_cooldowns = {"faucon": 1900000000}
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    click2 = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await open_btn.callback(click2)  # type: ignore[arg-type]
    content2 = str(click2.response.message.content or "") if click2.response.message else ""
    assert click2.response.ephemeral is True
    assert "ne disposez pas de sabotage ni de Faucon" in content2
    # PATCH 1.0 item 1: the notice rides the fenêtre unique registry —
    # a repeated refusal REPLACES the previous one instead of stacking.
    stored = _EPHEMERAL_WINDOWS.get("king-a")
    assert stored is not None and stored[0] is click2
    click2b = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await open_btn.callback(click2b)  # type: ignore[arg-type]
    assert click2.original_deleted is True
    stored2 = _EPHEMERAL_WINDOWS.get("king-a")
    assert stored2 is not None and stored2[0] is click2b
    # With a landed faucon the menu DOES open (the guard passes) — and
    # the panel's opening closes the stale refusal window.
    kingdom.voliere_cooldowns = {"faucon": 0}
    await bundle.store.upsert_kingdom(kingdom.to_mongo())
    click3 = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await open_btn.callback(click3)  # type: ignore[arg-type]
    panel3 = str(click3.response.message.content or "") if click3.response.message else ""
    assert "0/2" in panel3
    assert click2b.original_deleted is True  # the stale notice is closed
    assert "king-a" not in _EPHEMERAL_WINDOWS  # the panel is never registered
    _EPHEMERAL_WINDOWS.pop("king-a", None)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_meeting_open_sweep_clears_the_sabotage_dm_buttons(monkeypatch) -> None:
    """dm-expiration-v1 (Drasah 27/10): a meeting that opens on the
    T-default (a camp never settled) re-edits both DMs WITHOUT their
    view — the dead phase's 🗡️/⚔️ buttons disappear from the MP, the UI
    mirrors the server state instead of answering a rejection."""

    class _Attack:
        id = "a-1"
        attacker_lord_id = "111"
        defender_lord_id = "222"
        ai_defender = False
        map_key = "fortress"
        pickban = {  # noqa: RUF012
            "attacker_dm": {"channel_id": 10, "message_id": 11},
            "defender_dm": {"channel_id": 20, "message_id": 21},
        }

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel

    edits: list[tuple[dict, str, object]] = []
    reposts: list[str] = []

    async def fake_edit(bot, ref, content, view, files=None):  # type: ignore[no-untyped-def]
        edits.append((dict(ref), content, view))
        return True

    async def fake_dm(bot, player_id, content, view=None):  # type: ignore[no-untyped-def]
        reposts.append(str(player_id))

    monkeypatch.setattr(panel, "_edit_dm_message_safe", fake_edit)
    monkeypatch.setattr(panel, "_dm_user_safe", fake_dm)

    await panel._sweep_sabotage_dms_on_open_safe(None, _Attack(), panel._strings("fr"))
    # Both stored DMs are re-edited, view STRIPPED (the buttons go away).
    assert len(edits) == 2
    assert all(view is None for _, _, view in edits)
    assert all("Phase de sabotage terminée — fortress" in content for _, content, _ in edits)
    # Both edits succeeded — no fallback repost, no stacking bubble.
    assert reposts == []


async def test_room_ping_gets_the_winner_stamped_at_the_resolution() -> None:
    """sabotage-ux-v1: the « ⚔️ … à vos armes ! » ping of the room is
    recorded when the room opens and gets the 🏆 winner stamped at
    the resolution — once, never duplicated."""
    import asyncio

    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        _declare_room_score,
        _open_battle_room,
        _strings,
    )
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _get_wiring

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.open_meeting()
    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    bot = _FakeBot(bundle)
    bot.guilds = [guild]  # type: ignore[assignment]
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    attack.ai_defender = True  # a solo declaration resolves at once
    await bundle.store.upsert_attack(attack.to_mongo())
    await _open_battle_room(bot, guild, attack, bundle.kingdoms, "fr")

    # The ping refs are recorded — the stamp can find the message.
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    score = dict(fresh.score or {})
    assert score.get("room_ping_channel_id") == salon.id
    assert score.get("room_ping_message_id")
    ping = next(m for m in salon.messages if "à vos armes" in str(m.content or ""))
    assert "🏆" not in str(ping.content or "")

    # The solo declaration resolves — the ping wears the winner.
    wiring = _get_wiring()
    strings = _strings("fr")
    declare = MockInteraction(user=MockUser(id="king-a"), guild=guild, channel=salon, locale="fr", client=bot)
    await _declare_room_score(declare, wiring, strings, str(attack.id), "king-a", aquitaine, game_id="aoe2de://lobby")  # type: ignore[arg-type]
    assert "🏆 Vainqueur : **Aquitaine**" in str(ping.content or "")

    # A second stamp never duplicates the line.
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _stamp_room_ping_winner_safe

    await _stamp_room_ping_winner_safe(bot, fresh, "Aquitaine", strings)
    assert str(ping.content or "").count("🏆 Vainqueur") == 1
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_reveal_renames_the_salon_to_the_true_attacker() -> None:
    """admin-tools-v1 (Drasah 30/10): the attack salon wears the COVER
    name until the T-15 reveal — then it is renamed in place to the REAL
    attacker vs the defender (best effort, never blocking the notes)."""
    import asyncio

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.models import LordRole

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.kingdoms.enroll("king-c", "King C", LordRole.KING, proposed_name="Provence")
    await bundle.draw(28)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    attack = await bundle.attacks.declare_attack(
        "king-a",
        (await bundle.owner_maps(bourgogne))[0],
        "",
        cover_lord_id="king-c",
        now=datetime(2026, 10, 11, 12, 0, tzinfo=UTC),
    )
    await bundle.attacks.respond_defense(
        attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC)
    )
    guild = MockGuild()
    salon = await guild.create_text_channel("🗡️-king-c-vs-bourgogne")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    played = await panel._run_reveals_safe(guild, bundle.attacks, bundle.kingdoms, panel._strings("fr"))
    assert played == 1
    # the cover name is GONE — the salon says the real attacker.
    assert salon.name == "🗡️-king-a-vs-bourgogne"
    reveal = [m for m in salon.messages if "La couverture tombe" in str(m.content or "")]
    assert reveal
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_meeting_room_sweeps_the_pre_room_announces() -> None:
    """salon-nettoyage-v1: opening the battle room deletes the declare + reveal notes."""
    import asyncio

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack = await bundle.fresh_attack()
    await bundle.attacks.respond_defense(attack.id, "lord-b", now=datetime(2026, 10, 11, 12, 30, tzinfo=UTC))

    _wiring(bundle)
    guild = MockGuild()
    salon = await guild.create_text_channel("salon d'attaque")
    await bundle.attacks.record_attack_channel(attack.id, salon.id)
    base = await salon.send("⚔️ a lancé une attaque…")
    await bundle.attacks.record_attack_base_message(attack.id, base.id)

    # The reveal note lands and gets recorded as a sweepable ref.
    played = await panel._run_reveals_safe(guild, bundle.attacks, bundle.kingdoms, panel._strings("fr"))
    assert played == 1
    notes = [m for m in salon.messages if "Bataille en vue" in str(m.content or "")]
    assert len(notes) == 1
    fresh = await bundle.attacks.get_attack(attack.id)
    assert fresh.score["reveal_message_refs"] == [{"channel_id": salon.id, "message_id": notes[0].id}]

    # Opening the room sweeps the declare announce and the reveal note.
    bot = _FakeBot(bundle)
    bot.guilds = [guild]
    await panel._open_battle_room(bot, guild, fresh, bundle.kingdoms, "fr")
    opened = await bundle.attacks.get_attack(attack.id)
    assert opened.score["room_channel_id"]
    assert base.deleted is True
    assert notes[0].deleted is True
    meeting = next(m for m in salon.messages if m.id == opened.score["meeting_message_id"])
    assert getattr(meeting, "deleted", None) is None
    pings = [m for m in salon.messages if "à vos armes" in str(m.content or "")]
    assert len(pings) == 1
    assert getattr(pings[0], "deleted", None) is None

    # Re-opening never replays the sweep (early return on room_channel_id).
    await panel._open_battle_room(bot, guild, opened, bundle.kingdoms, "fr")
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_declare_flow_zero_embuscade_opens_the_kingdom_step() -> None:
    """embuscade-v1: zero bought Embuscade — NO mode view, the kingdom step first."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    view = (click.response.message or None) and click.response.message.view
    assert view is not None
    ids = [c.custom_id for c in view.children]
    assert "kingdoms:attack:choose:kingdom" in ids
    assert "kingdoms:attack:mode:embuscade" not in ids


async def test_declare_flow_embuscade_mode_declares_the_ambush() -> None:
    """embuscade-v1: bought cards open the MODE view; the ambush rides its own flow."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, embuscade=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    # Step 0 — the mode view (classic vs embuscade, delays in the labels).
    mode_view = click.response.message and click.response.message.view
    assert mode_view is not None
    ids = [c.custom_id for c in mode_view.children]
    assert "kingdoms:attack:mode:classic" in ids
    assert "kingdoms:attack:mode:embuscade" in ids
    embuscade_button = next(
        c for c in mode_view.children if c.custom_id == "kingdoms:attack:mode:embuscade"
    )
    assert "1h" in str(embuscade_button.label)
    mode_click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:mode:embuscade",
        locale="fr",
    )
    await embuscade_button.callback(mode_click)  # type: ignore[arg-type]
    assert mode_click.response.message is not None
    # Step 1 — the kingdom select, embuscade flavour (armed line).
    kingdom_view = mode_click.response.message.view
    assert kingdom_view is not None
    assert "Embuscade armée" in str(mode_click.response.message.content)
    kingdom_select = next(
        c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom"
    )
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    assert step.response.message is not None
    # Step 2 — the EMBUSCADE territory select ONLY (no classic select).
    territory_view = step.response.message.view
    assert territory_view is not None
    tids = [c.custom_id for c in territory_view.children]
    assert "kingdoms:attack:embuscade:territory" in tids
    assert "kingdoms:attack:choose:territory" not in tids
    embuscade_select = next(
        c for c in territory_view.children if c.custom_id == "kingdoms:attack:embuscade:territory"
    )
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await embuscade_select.callback(step2)  # type: ignore[arg-type]
    # The ambush declared: exceptional attack, card consumed, wizard dead.
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert attacks[0].embuscade is True
    assert str(attacks[0].state.value) == "declared"
    assert attacks[0].map_key == target
    assert click.original_deleted is True
    fresh = await bundle.attacks.technology_state(aquitaine)
    assert fresh.purchases.get("embuscade") == 0
    assert step2.followup.messages
    assert "Embuscade déclarée" in (step2.followup.messages[0].content or "")


async def test_declare_flow_mode_classic_stays_a_normal_attack() -> None:
    """embuscade-v1: the classic button keeps the normal flow — the card stays."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    aquitaine = await bundle.kingdom_id("Aquitaine")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    await bundle.stock(aquitaine, embuscade=1)
    _wiring(bundle)
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    await button.callback(click)  # type: ignore[arg-type]
    mode_view = click.response.message and click.response.message.view
    assert mode_view is not None
    classic_button = next(
        c for c in mode_view.children if c.custom_id == "kingdoms:attack:mode:classic"
    )
    mode_click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),  # type: ignore[arg-type]
        custom_id="kingdoms:attack:mode:classic",
        locale="fr",
    )
    await classic_button.callback(mode_click)  # type: ignore[arg-type]
    kingdom_view = mode_click.response.message and mode_click.response.message.view
    assert kingdom_view is not None
    kingdom_select = next(
        c for c in kingdom_view.children if c.custom_id == "kingdoms:attack:choose:kingdom"
    )
    step = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [bourgogne]},
        locale="fr",  # type: ignore[arg-type]
    )
    await kingdom_select.callback(step)  # type: ignore[arg-type]
    territory_view = step.response.message and step.response.message.view
    territory_select = next(
        c for c in territory_view.children if c.custom_id == "kingdoms:attack:choose:territory"
    )
    step2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        data={"values": [target]},
        locale="fr",  # type: ignore[arg-type]
    )
    await territory_select.callback(step2)  # type: ignore[arg-type]
    attacks = await bundle.attacks.attacks()
    assert len(attacks) == 1
    assert not attacks[0].embuscade
    assert str(attacks[0].state.value) == "declared"
    # The bought Embuscade card is NOT consumed by a classic attack.
    fresh = await bundle.attacks.technology_state(aquitaine)
    assert fresh.purchases.get("embuscade") == 1


async def test_info_button_sends_the_rundown_sheet() -> None:
    """attaque-ergonomie-v1: the former declaration thread's « Déroulé
    de l'attaque & droits du défenseur » sheet lives in the ⚔️ info
    button of the declare panel (ephemeral, single window)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackInfoButton,
        _strings,
    )

    button = KingdomAttackInfoButton(_strings("fr")["attack_info_button"])
    assert button.item.custom_id == "kingdoms:attack:info"
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        custom_id="kingdoms:attack:info",
        locale="fr",
    )
    await button.callback(click)  # type: ignore[arg-type]
    assert click.response.ephemeral is True
    assert "Déroulé de l'attaque" in str(click.response.message and click.response.message.content or "")
    assert "Siège" in str(click.response.message and click.response.message.content or "")


async def test_declare_flow_second_click_closes_the_previous_window() -> None:
    """attaque-ergonomie-v1 (Drasah 28/10): every attack-flow entry
    answers through the mod's single ephemeral window — a repeated
    declare click CLOSES the previous « Choisis le royaume » view
    instead of stacking a second one under the panel."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _EPHEMERAL_WINDOWS
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomAttackDeclareButton,
        _strings,
    )

    _EPHEMERAL_WINDOWS.clear()
    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    _wiring(bundle)
    button = KingdomAttackDeclareButton(_strings("fr")["declare_button"])
    click1 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    await button.callback(click1)  # type: ignore[arg-type]
    assert click1.response.message is not None
    click2 = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        custom_id="kingdoms:attack:declare",
        locale="fr",
    )
    await button.callback(click2)  # type: ignore[arg-type]
    # The FIRST window was closed by the second click — one live
    # ephemeral per player, no stacking.
    assert click1.original_deleted is True
    assert click1.response.message is None
    assert click2.response.message is not None
    _EPHEMERAL_WINDOWS.clear()


async def test_scoreboard_without_id_is_one_centered_waiting_line() -> None:
    """attaque-ergonomie-v1 (Drasah 28/10): no game ID yet — ONE
    centered « 🔗 en attente de l'ID » line, EMPTY side statuses and
    NO duplicated « 🆔 ID à renseigner » reminder."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _room_body_text, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    attack.lobby_url = ""
    body = await _room_body_text(attack, bundle.kingdoms, _strings("fr"))
    assert "🔗 en attente de l'ID" in body
    assert body.count("en attente de l'ID") == 1
    assert "ID à renseigner" not in body
    assert "en attente du Prêt" not in body


async def test_scoreboard_drops_the_text_vs_and_the_civs_line() -> None:
    """blasons-affichage-v1 (Drasah 28/10): the painted banner above the
    scoreboard already carries the names AND the civilizations — the
    monospace block keeps the pseudos + Prêt states, no text « VS »
    separator, no civs line (the banner says it)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _room_body_text, _strings

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    attack.lobby_url = "https://example.com/lobby"
    attack.pickban = {
        "attacker_picks": ["francs", "celtes", "goths"],
        "defender_picks": ["huns", "perses", "bretons"],
    }
    body = await _room_body_text(attack, bundle.kingdoms, _strings("fr"))
    # The played civs never ride the text block anymore.
    assert "francs" not in body
    assert "huns" not in body
    # No text VS between the pseudos — the banner's badge says it.
    assert " VS " not in body
    # The scoreboard keeps its map, ID and Prêt states.
    assert "⚔️" in body
    assert "🆔" in body
    assert "en attente du Prêt" in body


async def test_draft_view_button_lists_the_sniped_picks() -> None:
    """blasons-affichage-v1 (Drasah 28/10): the 📜 « Voir la draft »
    button answers Drasah's TWO-COLUMN embed — each side's 3 civs
    numbered 1️⃣2️⃣3️⃣ under its player's name, the sabotaged picks
    struck through (~~civ~~ ❌)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import (
        KingdomDraftViewButton,
        _strings,
    )

    bundle = PhasesBundle()
    await bundle.launch_season()
    await bundle.draw(21)
    attack = await bundle.fresh_attack()
    # A resolved draft: 3 picks per side, one snipe on each side.
    attack.pickban = {
        "attacker_picks": ["francs", "celtes", "goths"],
        "defender_picks": ["huns", "perses", "bretons"],
        "attacker_sabotages": ["huns"],  # the attacker struck the defender
        "defender_sabotages": ["goths"],  # the defender struck the attacker
    }
    await bundle.store.upsert_attack(attack.to_mongo())
    _wiring(bundle)
    strings = _strings("fr")
    button = KingdomDraftViewButton(str(attack.id), strings["draft_button"])
    assert str(button.item.custom_id).startswith("kingdoms:attack:room:draft:")
    click = MockInteraction(  # type: ignore[arg-type]
        user=MockUser(id="king-a"),
        guild=MockGuild(),
        locale="fr",
    )
    await button.callback(click)  # type: ignore[arg-type]
    assert click.response.ephemeral is True
    embed = click.response.message and click.response.message.embed
    assert embed is not None
    assert "Draft" in str(embed.title)
    assert len(embed.fields) == 2
    left, right = embed.fields
    assert left.name.startswith("⚔️") and right.name.startswith("🛡️")
    # Every side's civs ride the two columns, snipes struck through.
    assert "1️⃣" in left.value and "2️⃣" in left.value and "3️⃣" in left.value
    assert "francs" in left.value and "celtes" in left.value
    assert "~~goths~~ ❌" in left.value
    assert "perses" in right.value and "bretons" in right.value
    assert "~~huns~~ ❌" in right.value


async def test_meeting_files_wear_the_alliances_mosaic_banner(monkeypatch) -> None:
    """blasons-affichage-v1 (Drasah 28/10): the meeting message wears ONE
    composite banner — [crest + nom attaquant][VS][crest + nom défenseur],
    the Alliances mosaic style — and degrades to the text alone when the
    banner cannot be built."""
    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel

    class _File:
        filename = "vs_meeting.png"

    async def fake_banner(attacker_civ, defender_civ):
        return _File()

    monkeypatch.setattr(panel, "meeting_vs_mosaic_file", fake_banner)
    pickban = {
        "attacker_picks": ["francs"],
        "defender_picks": ["huns"],
        "attacker_sabotages": [],
        "defender_sabotages": [],
    }
    files = await panel._meeting_files(pickban)
    assert [f.filename for f in files] == ["vs_meeting.png"]
    # Banner unbuildable (no civ resolvable) → the text alone.
    async def fake_none(attacker_civ, defender_civ):
        return None

    monkeypatch.setattr(panel, "meeting_vs_mosaic_file", fake_none)
    files = await panel._meeting_files(pickban)
    assert files == []


async def test_vs_badge_file_reads_the_local_asset(monkeypatch, tmp_path) -> None:
    """blasons-vs-v1: the badge resolves assets/bataille/vs.png — and
    stays silent (None) when the PNG is not there."""
    import io as _io

    import discord as _discord

    from kingdoms.mods.kingdoms import civ_crests

    monkeypatch.setattr(
        civ_crests, "_VS_BADGE_PATH", tmp_path / "vs.png"
    )
    assert civ_crests.vs_badge_file() is None
    (tmp_path / "vs.png").write_bytes(b"png")
    badge = civ_crests.vs_badge_file()
    assert badge is not None and badge.filename == "vs.png"


async def test_pickban_and_reveal_dms_carry_the_closing_time(monkeypatch) -> None:
    """dm-fenetres-v1 (Drasah 28/10, règle transverse « toute fenêtre
    affiche son délai »): le DM de pick & ban affiche l'heure de
    FERMETURE de la fenêtre, le DM de reveal affiche l'heure du RDV
    (heure de Paris)."""
    from datetime import UTC, datetime

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel

    fr = panel._strings("fr")
    en = panel._strings("en")
    assert "{deadline}" in fr["pickban_dm"] and "Fermeture de la fenêtre" in fr["pickban_dm"]
    assert "{deadline}" in en["pickban_dm"] and "Window closes" in en["pickban_dm"]
    assert "{deadline}" in fr["pickban_reveal_dm"] and "RDV : {deadline}" in fr["pickban_reveal_dm"]
    assert "{deadline}" in en["pickban_reveal_dm"] and "Meeting: {deadline}" in en["pickban_reveal_dm"]

    class _Attack:
        id = "a-1"
        attacker_lord_id = "111"
        defender_lord_id = "222"
        ai_defender = False
        map_key = "fortress"
        expires_at = datetime(2026, 10, 28, 20, 40, tzinfo=UTC)
        pickban = {  # noqa: RUF012
            "attacker_picks": ["francs", "goths", "huns"],
            "attacker_dm": {"channel_id": 10, "message_id": 11},
            "defender_dm": {"channel_id": 20, "message_id": 21},
        }

    contents: list[str] = []

    async def fake_edit(bot, ref, content, view, files=None):  # type: ignore[no-untyped-def]
        contents.append(str(content))
        return True

    async def fake_dm(bot, player_id, content, view=None):  # type: ignore[no-untyped-def]
        contents.append(str(content))

    async def fake_mosaic(entries, filename="reveal.png", **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(panel, "_edit_dm_message_safe", fake_edit)
    monkeypatch.setattr(panel, "_dm_user_safe", fake_dm)
    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)

    await panel._edit_pickban_dms_reveal(None, _Attack(), None, fr)
    # Both DM edits wear the RDV time in Paris (20:40 UTC → 21:40 Paris).
    assert len(contents) == 2
    assert all("28/10 21:40" in content for content in contents)
    assert all("RDV : 28/10 21:40" in content for content in contents)
def test_reveal_civs_order_is_stable_across_refreshes() -> None:
    """sabotage-dm-fresh-v1 (Drasah 30/10): the reveal order is SEEDED
    — the DM mosaic never reorders between the initial reveal and a
    post-pick refresh (a fresh shuffle jumped at each snipe)."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _reveal_civs

    picks = ["francs", "bretons", "goths", "huns", "perses"]
    first = _reveal_civs("a-1:defender", picks)
    assert first == _reveal_civs("a-1:defender", picks)
    assert sorted(first) == sorted(picks)
    assert _reveal_civs("a-1:attacker", picks)  # another seed resolves too


async def test_sabotage_dm_button_wears_the_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sabotage-dm-fresh-v1 (Drasah 30/10): the 🗡️ DM button is BLUE
    while the side owns a usable charge under the 2 cap, RED once the
    stock is dry or the cap is reached."""
    import discord

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=1)
    _wiring(bundle)
    wiring = _panel_wiring()
    strings = panel._strings("fr")

    async def fake_mosaic(entries, filename="reveal.png", **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)
    view = await panel._sabotage_dm_view(wiring, attack, "attacker", strings)
    assert view.children[0].item.style is discord.ButtonStyle.primary

    # The 2-per-attack cap is reached — the button turns red.
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    pickban = dict(fresh.pickban)
    pickban["attacker_sabotages"] = ["huns", "perses"]
    fresh.pickban = pickban
    await bundle.store.upsert_attack(fresh.to_mongo())
    view = await panel._sabotage_dm_view(wiring, fresh, "attacker", strings)
    assert view.children[0].item.style is discord.ButtonStyle.danger


async def test_sabotage_pick_refreshes_the_reveal_dm_and_asks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sabotage-dm-fresh-v1 (Drasah 30/10): the pick edits the Reveal DM
    IN PLACE (crossed mosaic + fresh stock) and the panel asks « one
    more ? » — Oui (green) re-opens the action menu in the same panel."""
    import asyncio

    import discord

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=2)
    _wiring(bundle)
    wiring = _panel_wiring()
    fresh = next(a for a in await bundle.attacks.attacks() if a.id == attack.id)
    pickban = dict(fresh.pickban)
    pickban["attacker_dm"] = {"channel_id": 10, "message_id": 11}
    fresh.pickban = pickban
    await bundle.store.upsert_attack(fresh.to_mongo())

    dm_edits: list[dict[str, Any]] = []

    async def fake_edit(bot, ref, content, view, files=None):  # type: ignore[no-untyped-def]
        dm_edits.append({"ref": dict(ref), "content": str(content)})
        return True

    async def fake_mosaic(entries, filename="reveal.png", **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(panel, "_edit_dm_message_safe", fake_edit)
    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)

    strings = panel._strings("fr")
    view = await panel._sabotage_civ_select_view(
        wiring, str(attack.id), "attacker", "techno", strings
    )
    assert view is not None
    select = view.children[0]
    select.values = ["huns"]
    click = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await select.callback(click)  # type: ignore[arg-type]

    # The Reveal DM was re-edited IN PLACE — the stock went 2 → 1.
    assert len(dm_edits) == 1
    assert dm_edits[0]["ref"] == {"channel_id": 10, "message_id": 11}
    assert "Sabotage : 1" in dm_edits[0]["content"]
    # The panel asks for a 2nd sabotage — green Oui, red Non.
    content = str(click.response.message.content or "")
    assert "1/2" in content
    assert "Veux-tu saboter une autre" in content
    confirm = click.response.message.view
    assert confirm is not None and len(confirm.children) == 2
    assert confirm.children[0].style is discord.ButtonStyle.success
    assert confirm.children[1].style is discord.ButtonStyle.danger

    # Oui re-opens the action menu in the SAME panel (stock left: 1).
    yes_click = MockInteraction(
        user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr"
    )
    await confirm.children[0].callback(yes_click)  # type: ignore[arg-type]
    assert "Choisis la civilisation" in str(yes_click.response.message.content or "")
    assert yes_click.response.message.view is not None
    assert list(yes_click.response.message.view.children)
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)


async def test_sabotage_dry_pick_closes_the_panel_without_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """sabotage-dm-fresh-v1: the LAST charge (dry stock) closes the
    panel directly — no question, the DM carries the récap alone."""
    import asyncio

    from kingdoms.mods.kingdoms import kingdom_attack_panel as panel
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring as _panel_wiring

    bundle = ScoreBundle()
    await bundle.launch_season()
    await bundle.draw(26)
    attack, aquitaine, _bourgogne = await bundle.meeting()
    await bundle.stock(aquitaine, sabotage=1)
    _wiring(bundle)
    wiring = _panel_wiring()

    dm_edits: list[str] = []

    async def fake_edit(bot, ref, content, view, files=None):  # type: ignore[no-untyped-def]
        dm_edits.append(str(content))
        return True

    async def fake_mosaic(entries, filename="reveal.png", **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(panel, "_edit_dm_message_safe", fake_edit)
    monkeypatch.setattr(panel, "civ_crest_mosaic_file", fake_mosaic)

    strings = panel._strings("fr")
    view = await panel._sabotage_civ_select_view(
        wiring, str(attack.id), "attacker", "techno", strings
    )
    assert view is not None
    select = view.children[0]
    select.values = ["huns"]
    click = MockInteraction(user=MockUser(id="king-a"), guild=MockGuild(), channel=None, locale="fr")
    await select.callback(click)  # type: ignore[arg-type]

    assert len(dm_edits) == 1
    assert "Sabotage : 0" in dm_edits[0]
    content = str(click.response.message.content or "")
    assert "1/2" in content
    assert "Veux-tu saboter une autre" not in content
    assert click.response.message.view is None
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()
    await asyncio.sleep(0)
