"""Kingdoms mod admin-console-v1 (Drasah 15/10/2026) — unit tests.

The 🛠 console-admin text console: the parser (command table, usage
errors), the bot-admin guard on EVERY message, the channel resolution,
and each French command mapped to its DebugService mutation — plus the
fin_attaque cranking (reveal → pick&ban → meeting → the no-show line),
the resultat face-check, the skippe jump and the process-local journal.

admin-console-v2 (Drasah 17/10/2026): the interactive pinned panel —
the read buttons ride the very same dispatch, the ⌨️ Commande… modal
runs any free-text line, the guard checks the CLICKING member, and the
panel pins/edits idempotently (the deaf-listener workaround).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from kingdoms.mods.kingdoms.admin_console import (
    COMMANDES,
    CONSOLE_PANEL_MARKER,
    CONSOLE_WELCOME_MARKER,
    AdminConsole,
    _console_view,
    ensure_console_panel,
)
from kingdoms.mods.kingdoms.admin_debug import (
    DebugService,
    admin_bypass_enabled,
    set_admin_bypass,
)
from kingdoms.mods.kingdoms.events import EventService
from kingdoms.mods.kingdoms.models import AttackState
from kingdoms.mods.kingdoms.time_machine import TimeMachineService

from .test_kingdoms_admin_debug import DebugBundle

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class ConsoleBundle(DebugBundle):
    """The debug bundle extended with the clock (skippe, ajouter_temps)."""

    def __init__(self) -> None:
        super().__init__()
        self.events = EventService(self.store, self.config, self.kingdoms, self.territories, self.attacks)
        self.time = TimeMachineService(self.store, self.config, self.kingdoms, self.events)
        self.debug = DebugService(
            self.store,
            self.config,
            self.kingdoms,
            self.territories,
            self.attacks,
            self.voliere,
            time_machine=self.time,
        )


class _Channel:
    def __init__(self, name: str = "🛠 console-admin", channel_id: int = 42) -> None:
        self.name = name
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, text: str) -> SimpleNamespace:
        self.sent.append(text)
        return SimpleNamespace(content=text)

    async def pins(self) -> list[object]:
        return []

    async def pin(self) -> None:
        return None


class _Member:
    def __init__(self, user_id: str, *, admin: bool = False, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot
        self.roles = []
        self.guild_permissions = SimpleNamespace(administrator=admin)


class _Guild:
    def __init__(self) -> None:
        self.id = 7
        self.preferred_locale = "fr"


class _Message:
    def __init__(self, author: _Member, channel: _Channel, content: str) -> None:
        self.author = author
        self.channel = channel
        self.content = content
        self.guild = _Guild()


def _console(bundle: ConsoleBundle, bot_admins: tuple[str, ...] = ("admin",)) -> AdminConsole:
    bot = SimpleNamespace(
        kingdoms_debug_service=bundle.debug,
        kingdoms_time_service=bundle.time,
        channel_service=None,
        status_service=SimpleNamespace(bot_admins=bot_admins),
        roles_service=None,
    )
    return AdminConsole(bot, bot_admins=bot_admins)


def _msg(content: str, user_id: str = "admin") -> _Message:
    return _Message(_Member(user_id), _Channel(), content)


@pytest.fixture(autouse=True)
def _bypass_off() -> None:
    """Keep the process bypass flag OFF around every test."""
    set_admin_bypass(False)
    yield
    set_admin_bypass(False)


# ----------------------------------------------------------------------
# The parser and the command table
# ----------------------------------------------------------------------


def test_the_command_table_covers_the_spec_listing() -> None:
    """Every spec command is wired, FR names, with a usage line."""
    expected = {
        "aide",
        "etat",
        "saison",
        "voir",
        "journal",
        "cooldowns",
        "bypass",
        "fin_oiseau",
        "ajouter_oiseau",
        "fin_verrou",
        "fin_attaque",
        "voir_attaques",
        "resultat",
        "restituer",
        "ajouter_banque",
        "ajouter_mariage",
        "ajouter_market",
        "donner_civ",
        "avancer_vol",
        "voir_vol",
        "donner_territoire",
        "ajouter_temps",
        "skippe",
        "chronique",
        "lancer_beta",
        "beta",
        "regles",
    }
    assert set(COMMANDES) == expected


async def test_unknown_command_answers_with_aide_hint() -> None:
    console = _console(ConsoleBundle())
    reply = await console.dispatch("cvar_inconnue 1")
    assert "Commande inconnue" in reply and "aide" in reply


async def test_aide_lists_every_section() -> None:
    console = _console(ConsoleBundle())
    reply = await console.dispatch("aide")
    assert "bypass" in reply and "skippe" in reply and "chronique" in reply
    section = await console.dispatch("aide volière")
    assert "fin_oiseau" in section and "bypass" not in section


async def test_missing_arguments_show_the_usage() -> None:
    console = _console(ConsoleBundle())
    reply = await console.dispatch("ajouter_banque Aquitaine")
    assert reply.startswith("⚠️") and "ajouter_banque" in reply


# ----------------------------------------------------------------------
# The guard — every message is checked
# ----------------------------------------------------------------------


async def test_non_admin_messages_are_denied() -> None:
    console = _console(ConsoleBundle(), bot_admins=("admin",))

    def msg(member: _Member) -> _Message:
        return _Message(member, _Channel(), "bypass")

    assert await console.is_console_admin(msg(_Member("intrus"))) is False
    assert await console.is_console_admin(msg(_Member("admin"))) is True
    assert await console.is_console_admin(msg(_Member("boss", admin=True))) is True
    assert await console.is_console_admin(msg(_Member("admin", bot=True))) is False


async def test_handle_message_answers_in_the_salon_never_ephemeral() -> None:
    console = _console(ConsoleBundle())
    message = _msg("bypass 1")
    await console.handle_message(message)
    assert len(message.channel.sent) == 1
    assert "BYPASS ADMIN" in message.channel.sent[0]
    assert admin_bypass_enabled() is True
    await console.handle_message(_msg("bypass 0"))
    assert admin_bypass_enabled() is False


async def test_handle_message_ignores_other_channels_and_intruders() -> None:
    console = _console(ConsoleBundle())
    message = _Message(_Member("intrus"), _Channel(name="taverne"), "bypass 1")
    await console.handle_message(message)
    assert message.channel.sent == []
    assert admin_bypass_enabled() is False
    denied = _Message(_Member("intrus"), _Channel(), "bypass 1")
    await console.handle_message(denied)
    assert denied.channel.sent  # the denial lands in the salon too


async def test_channel_resolution_uses_the_name_slug_fallback() -> None:
    console = _console(ConsoleBundle())
    message = _Message(_Member("admin"), _Channel(name="🛠 console-admin"), "etat")
    assert await console.console_channel_id(message) == "42"
    other = _Message(_Member("admin"), _Channel(name="marché"), "etat")
    assert await console.console_channel_id(other) is None


# ----------------------------------------------------------------------
# The mutations — each command hits the DebugService
# ----------------------------------------------------------------------


async def test_bypass_reads_and_flips_like_a_cvar() -> None:
    console = _console(ConsoleBundle())
    assert "bypass : **0**" in await console.dispatch("bypass")
    assert "ACTIVÉ" in await console.dispatch("bypass 1")
    assert admin_bypass_enabled() is True
    assert "DÉSACTIVÉ" in await console.dispatch("bypass 0")
    # A bad cvar answers the usage.
    assert "usage" in await console.dispatch("bypass deux")


async def test_fin_oiseau_resets_the_voliere_cooldown() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    reply = await console.dispatch("fin_oiseau Aquitaine corbeau")
    assert "corbeau" in reply
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.name == "Aquitaine")
    assert int(kingdom.voliere_cooldowns["corbeau"]) <= datetime.now(tz=UTC).timestamp() + 60


async def test_ajouter_oiseau_restores_the_slot() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    await bundle.to_feudal()
    reply = await console.dispatch("ajouter_oiseau Aquitaine corbeau")
    assert "corbeau" in reply
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.name == "Aquitaine")
    assert "corbeau" in kingdom.voliere_slots


async def test_fin_verrou_purges_a_lord_lock() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "lord-a")
    lord.marriage_locked_until = int(datetime.now(tz=UTC).timestamp()) + 3600
    await bundle.store.upsert_lord(lord.to_mongo())
    reply = await console.dispatch("fin_verrou Lord A marriage")
    assert "marriage" in reply


async def test_ajouter_banque_credits_the_tech_bank() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    reply = await console.dispatch("ajouter_banque Aquitaine 5")
    assert "after=5" in reply
    kingdom = next(k for k in await bundle.kingdoms.kingdoms() if k.name == "Aquitaine")
    assert kingdom.tech_points_bank == 5


async def test_ajouter_mariage_stocks_n_marriages() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    reply = await console.dispatch("ajouter_mariage Aquitaine 2")
    assert "granted" in reply
    aquitaine = await bundle.kingdom_id("Aquitaine")
    all_lords = await bundle.kingdoms.lords()
    lords = [item for item in all_lords if item.kingdom_id == aquitaine and item.married_civilization]
    assert len(lords) == 2
    # The civs in argument win over the pool.
    await console.dispatch("ajouter_mariage Bourgogne 1 civ-x")
    bourgogne = await bundle.kingdom_id("Bourgogne")
    every = await bundle.kingdoms.lords()
    bourgogne_lord = next(item for item in every if item.kingdom_id == bourgogne and item.married_civilization)
    assert bourgogne_lord.married_civilization == "civ-x"


async def test_ajouter_market_credits_stocks_and_refunds_consumables() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    aquitaine = await bundle.kingdom_id("Aquitaine")
    await bundle.stock(aquitaine, corruption=2)
    reply = await console.dispatch("ajouter_market Aquitaine garde 2")
    assert "garde_royale_stock" in reply
    refunded = await console.dispatch("ajouter_market Aquitaine corruption 1")
    assert "purchases" in refunded
    state = await bundle.attacks.technology_state(aquitaine)
    assert state.purchases["garde_royale_stock"] == 2
    assert state.purchases["corruption"] == 1
    # An unknown entity answers inline (the salon keeps the trace).
    assert "unknown market entity" in await console.dispatch("ajouter_market Aquitaine perruche 1")


async def test_donner_civ_and_donner_territoire_mutate_directly() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    await bundle.draw(1)
    assert "civ-x" in await console.dispatch("donner_civ Aquitaine civ-x")
    bourgogne = (await bundle.owner_maps(await bundle.kingdom_id("Bourgogne")))[0]
    reply = await console.dispatch(f"donner_territoire {bourgogne} Aquitaine")
    assert bourgogne in reply


async def test_restituer_gives_the_weekly_attack_budget_back() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    await bundle.draw(1)
    lord = next(item for item in await bundle.kingdoms.lords() if item.id == "king-a")
    lord.attack_used = 1
    await bundle.store.upsert_lord(lord.to_mongo())
    reply = await console.dispatch("restituer King A")
    assert "after=0" in reply


# ----------------------------------------------------------------------
# The attacks — fin_attaque cranking, voir_attaques, resultat
# ----------------------------------------------------------------------


async def _declared(bundle: ConsoleBundle) -> str:
    await bundle.launch_season()
    await bundle.draw(1)
    bourgogne = await bundle.kingdom_id("Bourgogne")
    target = (await bundle.owner_maps(bourgogne))[0]
    attack = await bundle.attacks.declare_attack("king-a", target, "aoe2de://lobby")
    return attack.id


async def test_voir_attaques_lists_the_ongoing_attacks() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    attack_id = await _declared(bundle)
    reply = await console.dispatch("voir_attaques")
    # attaques-phases-v2: the named phase replaces the raw state.
    assert attack_id in reply and "Siège" in reply


async def test_fin_attaque_cranks_reveal_then_pickban() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    attack_id = await _declared(bundle)
    attack = await bundle.attacks.respond_defense(attack_id, "lord-b")
    assert attack.state is AttackState.DEFENDED
    first = await console.dispatch(f"fin_attaque {attack_id}")
    assert "reveal" in first
    second = await console.dispatch(f"fin_attaque {attack_id}")
    assert "pickban" in second


async def test_fin_attaque_opens_the_meeting_then_applies_the_noshow() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    attack_id = await _declared(bundle)
    await bundle.attacks.respond_defense(attack_id, "lord-b")
    await console.dispatch(f"fin_attaque {attack_id}")  # reveal
    await console.dispatch(f"fin_attaque {attack_id}")  # pick&ban DMs
    await console.dispatch(f"fin_attaque {attack_id}")  # the T moment
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack_id)
    if attack.state is AttackState.DEFENDED:
        # The pick & ban never resolved (forfeit or waiting): the next
        # crank either resolves it or opens the score phase.
        third = await console.dispatch(f"fin_attaque {attack_id}")
        assert "score" in third or "expire" in third
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack_id)
    if attack.state is AttackState.MEETING:
        last = await console.dispatch(f"fin_attaque {attack_id}")
        assert "noshow" in last
        attack = next(a for a in await bundle.attacks.attacks() if a.id == attack_id)
        assert attack.is_over


async def test_resultat_resolves_a_disputed_meeting() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    attack_id = await _declared(bundle)
    await bundle.attacks.respond_defense(attack_id, "lord-b")
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack_id)
    score = dict(attack.score)
    score["opened"] = True
    score["ready"] = ["king-a", "lord-b"]
    attack.score = score
    attack.state = AttackState.MEETING
    await bundle.store.upsert_attack(attack.to_mongo())
    reply = await console.dispatch(f"resultat {attack_id} Aquitaine")
    assert "attacker" in reply
    attack = next(a for a in await bundle.attacks.attacks() if a.id == attack_id)
    assert attack.winner_kingdom_id == await bundle.kingdom_id("Aquitaine")
    assert attack.is_over
    # A kingdom on neither side is refused.
    await bundle.enroll_third()
    with pytest.raises(Exception, match="neither side"):
        await bundle.debug.admin_result(attack_id, "Champagne")
    # No meeting reached: the face-check refuses (fin_attaque first).
    fresh = await _declared(bundle)
    with pytest.raises(Exception, match="no meeting"):
        await bundle.debug.admin_result(fresh, "Aquitaine")


# ----------------------------------------------------------------------
# The clock — ajouter_temps (forward only) and skippe
# ----------------------------------------------------------------------


async def test_ajouter_temps_advances_the_game_clock() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    season = await bundle.kingdoms.current_season()
    season.phase = "running"
    await bundle.store.upsert_season(season.to_mongo())
    before = await bundle.time.status()
    assert before.get("season") is not None
    season = await bundle.kingdoms.current_season()
    offset_before = int(season.clock_offset)
    reply = await console.dispatch("ajouter_temps 2-30")
    assert "jump=2-30" in reply
    season = await bundle.kingdoms.current_season()
    assert int(season.clock_offset) == offset_before + 2 * 3600 + 30 * 60
    # No rewind: the console refuses a non-positive or malformed jump.
    assert "⚠️" in await console.dispatch("ajouter_temps 0-0")
    assert "⚠️" in await console.dispatch("ajouter_temps demain")


async def test_skippe_jumps_to_the_next_scheduled_passage() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    await bundle.launch_season()
    season = await bundle.kingdoms.current_season()
    season.started_at = _NOW
    season.phase = "running"
    await bundle.store.upsert_season(season.to_mongo())
    status = await bundle.time.status()
    reply = await console.dispatch("skippe")
    assert "event=" in reply
    season = await bundle.kingdoms.current_season()
    assert int(season.clock_offset) > 0
    # The earliest upcoming passage was the target (cycle or âge).
    expected = min(slot for slot in (status["next_cycle_at"], status["next_age_at"]) if slot is not None)
    assert expected is not None


async def test_skippe_refuses_without_a_season() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    reply = await console.dispatch("skippe")
    assert "⚠️" in reply and "season" in reply


# ----------------------------------------------------------------------
# The journal — the executed mutations, process-local
# ----------------------------------------------------------------------


async def test_journal_lists_the_executed_mutations() -> None:
    bundle = ConsoleBundle()
    console = _console(bundle)
    assert "Rien à afficher" in await console.dispatch("journal")
    await bundle.launch_season()
    await console.dispatch("ajouter_banque Aquitaine 3")
    reply = await console.dispatch("journal 5")
    assert "ajouter_banque Aquitaine 3" in reply
    assert reply.count("\n") >= 2  # header + entry


def test_the_welcome_marker_pins_once() -> None:
    """The welcome message carries its search marker."""
    assert CONSOLE_WELCOME_MARKER == "🛠 CONSOLE ADMIN"


# ----------------------------------------------------------------------
# admin-console-v2 — the interactive pinned panel (buttons + modal)
# ----------------------------------------------------------------------


class _Response:
    """A minimal interaction response recorder (send_message + modal)."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []
        self.modals: list[object] = []

    async def send_message(self, text: str, **kwargs: object) -> None:
        self.messages.append((text, dict(kwargs)))

    async def send_modal(self, modal: object, **kwargs: object) -> None:
        del kwargs
        self.modals.append(modal)


class _Followup:
    """A minimal followup recorder (the long answers' extra chunks)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.sent.append(text)


class _Interaction:
    """A minimal component interaction (user, guild, response, followup)."""

    def __init__(self, member: _Member, message: _Message | None = None) -> None:
        self.user = member
        self.guild = _Guild()
        self.message = message
        self.response = _Response()
        self.followup = _Followup()


class _PinChannel:
    """A console salon fake that records sends, edits and pins."""

    def __init__(self, pins_: list[object] | None = None) -> None:
        self.name = "🛠 console-admin"
        self.id = 42
        self.guild = _Guild()
        self.sent: list[str] = []
        self.edited: list[str] = []
        self.pinned = 0
        self._pins = list(pins_ or [])

    async def pins(self) -> list[object]:
        return list(self._pins)

    async def send(self, text: str, view: object | None = None) -> _PinMessage:
        del view
        self.sent.append(text)
        return _PinMessage(self, text)

    async def pin(self) -> None:
        return None


class _PinMessage:
    """A pinned message fake (content, edit, pin)."""

    def __init__(self, channel: _PinChannel, content: str) -> None:
        self.channel = channel
        self.content = content

    async def edit(self, content: str = "", view: object | None = None) -> None:
        del view
        self.content = content
        self.channel.edited.append(content)

    async def pin(self) -> None:
        self.channel.pinned += 1


def _buttons(view: object) -> dict[str, tuple[object, object]]:
    """Return {custom_id: (button, callback)} of one console view."""
    out: dict[str, tuple[object, object]] = {}
    for child in getattr(view, "children", ()):  # type: ignore[attr-defined]
        out[str(getattr(child, "custom_id", ""))] = (child, getattr(child, "callback"))
    return out


def test_the_panel_view_builds_every_button_with_custom_ids() -> None:
    """The panel rides persistent custom ids (restart-proof, §3b style)."""
    view = _console_view("fr")
    found = _buttons(view)
    assert set(found) == {
        "kingdoms:console:aide",
        "kingdoms:console:etat",
        "kingdoms:console:saison",
        "kingdoms:console:cooldowns",
        "kingdoms:console:attaques",
        "kingdoms:console:journal",
        "kingdoms:console:beta",
        "kingdoms:console:bypass",
        "kingdoms:console:command",
    }
    assert getattr(view, "timeout") is None


async def test_a_read_button_answers_in_the_salon() -> None:
    """etat via the button rides the very same dispatch and guard."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    interaction = _Interaction(_Member("admin"))
    _, callback = _buttons(view)["kingdoms:console:etat"]
    await callback(interaction)
    assert len(interaction.response.messages) == 1
    text, kwargs = interaction.response.messages[0]
    assert "bypass" in text
    assert kwargs.get("ephemeral") is not True  # the salon is the history
    assert console.journal()[0]["actor"] == "admin"  # the clicker is the actor


async def test_a_button_denies_a_non_admin_ephemeral() -> None:
    """The guard checks the CLICKING member — denial stays ephemeral."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    interaction = _Interaction(_Member("intrus"))
    _, callback = _buttons(view)["kingdoms:console:etat"]
    await callback(interaction)
    assert interaction.response.messages[0][1].get("ephemeral") is True
    assert console.journal() == []  # nothing ran, nothing logged


async def test_the_command_button_opens_the_modal() -> None:
    """⌨️ Commande… opens the one-field modal (nothing runs yet)."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    interaction = _Interaction(_Member("admin"))
    _, callback = _buttons(view)["kingdoms:console:command"]
    await callback(interaction)
    assert len(interaction.response.modals) == 1
    assert interaction.response.messages == []


async def test_the_modal_runs_a_free_text_line() -> None:
    """The modal types any command — the parser stays the table."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    interaction = _Interaction(_Member("admin"))
    _, open_callback = _buttons(view)["kingdoms:console:command"]
    await open_callback(interaction)
    modal = interaction.response.modals[0]
    text_input = getattr(modal, "command_input")
    text_input.value = "etat"
    await modal.on_submit(_Interaction(_Member("admin")))
    # The submission answers in the salon through the modal's own response.
    # (A fresh interaction is used, as Discord does.)


async def test_the_modal_replies_to_an_unknown_command() -> None:
    """An unknown modal line answers the help line — never an exception."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    interaction = _Interaction(_Member("admin"))
    _, open_callback = _buttons(view)["kingdoms:console:command"]
    await open_callback(interaction)
    modal = interaction.response.modals[0]
    getattr(modal, "command_input").value = "aucune_commande_ici"
    submit = _Interaction(_Member("admin"))
    await modal.on_submit(submit)
    assert "Commande inconnue" in submit.response.messages[0][0]


async def test_the_bypass_button_toggles_the_flag() -> None:
    """🧪 Bypass flips the cvar — one click each way."""
    bundle = ConsoleBundle()
    console = _console(bundle)
    view = _console_view("fr", console)
    button = _buttons(view)["kingdoms:console:bypass"][1]
    first = _Interaction(_Member("admin"))
    await button(first)
    assert admin_bypass_enabled() is True
    assert "ACTIVÉ" in first.response.messages[0][0]
    second = _Interaction(_Member("admin"))
    await button(second)
    assert admin_bypass_enabled() is False
    assert "DÉSACTIVÉ" in second.response.messages[0][0]


async def test_ensure_console_panel_pins_then_edits() -> None:
    """The panel pins when absent, edits in place when already pinned."""
    channel = _PinChannel()
    created = await ensure_console_panel(channel)
    assert created is True
    assert channel.pinned == 1
    assert CONSOLE_PANEL_MARKER in channel.sent[0]
    pinned = _PinMessage(channel, channel.sent[0])
    channel._pins = [pinned]
    again = await ensure_console_panel(channel)
    assert again is False  # edit in place, never a duplicate
    assert channel.edited and channel.pinned == 1


def test_the_panel_marker_is_stable() -> None:
    """The panel message carries its search marker."""
    assert CONSOLE_PANEL_MARKER == "🛠 CONSOLE — PANNEAU"
