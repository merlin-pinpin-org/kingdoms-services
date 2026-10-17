"""The 🛠 console-admin console (admin-console-v1 + v2, Drasah 15→17/10/2026).

A Counter-Strike-Source-style console in a private Discord salon: the
admin types FRENCH cvar-style commands (``bypass 1``, ``fin_oiseau
Aquitaine corbeau``), the listener parses the message, calls the
``DebugService`` and answers IN THE SALON — the salon is the history,
nothing is ever ephemeral. It REPLACES the ``/kingdoms-admin debug``
slash subgroup (whose sync failed silently): the mutations stay in
``DebugService``, only the Discord surface changes.

Architecture (spec admin-console-v1): a listener ``on_message`` with
the bot-admin guard on EVERY message, a ``COMMANDES`` table (one line
per command — handler, usage, help, section) and a process-local
journal of the executed mutations (``journal [N]``).

⚠️ Requires the MESSAGE CONTENT intent (dev portal + the bot factory
enables it) — without it the listener sees empty messages. That is why
admin-console-v2 (Drasah 17/10) adds the INTERACTIVE PINNED PANEL: read
buttons + a ⌨️ Commande… modal ride component interactions, which work
WITHOUT the intent — the console answers even when the listener is
deaf. The classes of the panel live inside ``_console_view`` (they
resolve the console at click time, §3b restart-proof style).
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import discord

from kingdoms.mods.kingdoms.admin_debug import (
    BIRDS,
    LOCK_KINDS,
    DebugError,
    admin_bypass_enabled,
    set_admin_bypass,
)
from kingdoms.mods.kingdoms.service import KingdomsModError

logger = logging.getLogger("kingdoms.admin_console")

CONSOLE_CHANNEL_KEY = "kingdoms:console"
"""The channels-registry category of the console salon."""

CONSOLE_CHANNEL_SLUG = "console-admin"
"""The salon's name slug (« 🛠 console-admin » → « console-admin »)."""

CONSOLE_WELCOME_MARKER = "🛠 CONSOLE ADMIN"
"""The marker of the pinned welcome message (pin kept alive, best effort)."""

CONSOLE_PANEL_MARKER = "🛠 CONSOLE — PANNEAU"
"""The marker of the pinned interactive panel (admin-console-v2)."""

_JOURNAL_MAX = 100
_TIME_RE = re.compile(r"^(\d+)-(\d+)$")
_PARAMS_RE = re.compile(r"([^=,]+)=([^,]*)")

PHASES_FR: dict[str, str] = {
    "siege": "Siège",
    "draft": "Draft",
    "ready": "RDV",
    "score": "Score",
}
"""The console's French phase names (attaques-phases-v2)."""

_CONSOLE: "AdminConsole | None" = None
"""The process-wide console singleton (set at register time — the
admin-console-v2 panel buttons resolve it at click time, §3b
restart-proof style, so the pinned message survives every restart)."""


def _current_console() -> "AdminConsole | None":
    """Return the registered console (None outside a wired process)."""
    return _CONSOLE


# ----------------------------------------------------------------------
# The strings (FR first — the console is Drasah's private test salon)
# ----------------------------------------------------------------------

CONSOLE_STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        "welcome": (
            "# 🛠 CONSOLE ADMIN\n"
            "Console de test du mod Kingdoms — tape une commande, la réponse "
            "reste dans le salon (l'historique).\n"
            "• `aide` — la liste complète • `aide <section>` — une section\n"
            "• `etat` — bypass, saison, vols corbeau\n"
            "⚠️ Réservé aux bot admins — chaque mutation est loggée."
        ),
        "denied": "⚠️ Réservé aux bot admins — la console t'ignore.",
        "unknown": "⚠️ Commande inconnue : `{command}` — tape `aide`.",
        "usage": "⚠️ Usage : `{usage}`",
        "done": "✅ {report}",
        "bypass_read": "bypass : **{state}** (0 = gates actives, 1 = contournées)",
        "bypass_on": "🧪 BYPASS ADMIN **ACTIVÉ** — les gates des boutons joueurs sont contournées.",
        "bypass_off": "🧪 BYPASS ADMIN **DÉSACTIVÉ** — chaque gate s'applique à nouveau.",
        "no_season": "aucune saison en cours",
        "not_wired": "⚠️ Le mod Kingdoms n'est pas câblé (service absent) — la console est inactive.",
        "empty": "Rien à afficher.",
        "journal_header": "📖 Journal des {count} dernières mutations (processus, perdu au redémarrage) :",
        "journal_line": "{at} · `{command}` par <@{actor}> → {status} {report}",
        "help_header": "🛠 CONSOLE — {section}",
        "help_line": "• `{usage}` — {help}",
        "aide_sections": "Sections : {sections} — tape `aide <section>`.",
        "panel_body": (
            "# 🛠 CONSOLE — PANNEAU\n"
            "Boutons de lecture : la réponse arrive ici même. ⌨️ Commande… ouvre "
            "une fenêtre pour taper n'importe quelle commande (`fin_oiseau royaume tous`…).\n"
            "Écrire directement dans le salon marche toujours (si l'intent "
            "message-content est actif).\n"
            "⚠️ Réservé aux bot admins — chaque mutation est loggée."
        ),
        "modal_title": "Commande console",
        "modal_label": "Commande",
        "modal_placeholder": "ex. fin_oiseau Aquitaine tous",
        "cmd_empty": "⚠️ Commande vide — tape par ex. `etat`.",
        "btn_aide": "ℹ️ Aide",
        "btn_etat": "📊 État",
        "btn_saison": "📅 Saison",
        "btn_cooldowns": "⏱️ Cooldowns",
        "btn_attaques": "⚔️ Attaques",
        "btn_journal": "📖 Journal",
        "btn_command": "⌨️ Commande…",
        "btn_beta": "🚀 Beta",
        "btn_bypass": "🧪 Bypass",
    },
    "en": {
        "welcome": (
            "# 🛠 ADMIN CONSOLE\n"
            "Kingdoms test console — type a command, the answer stays in the "
            "salon (the history).\n"
            "• `help` — the full list • `help <section>` — one section\n"
            "• `state` — bypass, season, running corbeau thefts\n"
            "⚠️ Bot admins only — every mutation is logged."
        ),
        "denied": "⚠️ Bot admins only — the console ignores you.",
        "unknown": "⚠️ Unknown command: `{command}` — type `help`.",
        "usage": "⚠️ Usage: `{usage}`",
        "done": "✅ {report}",
        "bypass_read": "bypass: **{state}** (0 = gates on, 1 = skipped)",
        "bypass_on": "🧪 ADMIN BYPASS **ON** — the player-button gate checks are bypassed.",
        "bypass_off": "🧪 ADMIN BYPASS **OFF** — every gate check applies again.",
        "no_season": "no season is running",
        "not_wired": "⚠️ The Kingdoms mod is not wired — the console is inactive.",
        "empty": "Nothing to display.",
        "journal_header": "📖 Journal of the last {count} mutations (process-local, lost on restart):",
        "journal_line": "{at} · `{command}` by <@{actor}> → {status} {report}",
        "help_header": "🛠 CONSOLE — {section}",
        "help_line": "• `{usage}` — {help}",
        "aide_sections": "Sections: {sections} — type `help <section>`.",
        "panel_body": (
            "# 🛠 CONSOLE — PANEL\n"
            "Read buttons: the answer lands right here. ⌨️ Command… opens a window "
            "to type any command (`bypass 1`…).\n"
            "Typing directly in the salon keeps working (when the "
            "message-content intent is on).\n"
            "⚠️ Bot admins only — every mutation is logged."
        ),
        "modal_title": "Console command",
        "modal_label": "Command",
        "modal_placeholder": "e.g. bypass 1",
        "cmd_empty": "⚠️ Empty command — type e.g. `state`.",
        "btn_aide": "ℹ️ Help",
        "btn_etat": "📊 State",
        "btn_saison": "📅 Season",
        "btn_cooldowns": "⏱️ Cooldowns",
        "btn_attaques": "⚔️ Attacks",
        "btn_journal": "📖 Journal",
        "btn_command": "⌨️ Command…",
        "btn_beta": "🚀 Beta",
        "btn_bypass": "🧪 Bypass",
    },
}


def _console_strings(locale: Any) -> dict[str, str]:
    """Pick the string set for a guild locale (FR default, the salon's)."""
    if locale is None:
        return CONSOLE_STRINGS["fr"]
    return CONSOLE_STRINGS["fr" if str(locale).startswith("fr") else "en"]


# ----------------------------------------------------------------------
# Formatting helpers
# ----------------------------------------------------------------------


def _remaining(seconds: Any) -> str:
    """Render a remaining duration the compact French way (1j 2h 3m)."""
    if isinstance(seconds, timedelta):
        seconds = seconds.total_seconds()
    seconds = max(0, int(seconds))
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append(f"{days}j")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _fmt(value: Any) -> str:
    """Render one report value compactly (dict → k=v pairs, list → joins)."""
    if isinstance(value, dict):
        return " ".join(f"{k}={_fmt(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(item) for item in value)
    if isinstance(value, datetime):
        return value.strftime("%d/%m %H:%M")
    return str(value)


def _fmt_report(report: Any) -> str:
    """Render one console-mutation report dict as a compact line."""
    if not isinstance(report, dict):
        return _fmt(report)
    parts = []
    for key, value in report.items():
        if key == "now":
            continue
        if isinstance(value, dict):
            value = "{" + " ".join(f"{k}={_fmt(v)}" for k, v in value.items()) + "}"
        parts.append(f"{key}={_fmt(value)}")
    return " ".join(parts)


# ----------------------------------------------------------------------
# The command table
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """One console command: its handler, usage line, help and section."""

    usage: str
    help_fr: str
    help_en: str
    section: str
    handler: Any  # async (console: AdminConsole, args: list[str]) -> str


class AdminConsole:
    """The console runtime: guard, parse, dispatch, answer in the salon."""

    def __init__(
        self,
        bot: Any,
        *,
        bot_admins: tuple[str, ...] = (),
        roles_service: Any = None,
    ) -> None:
        self.bot = bot
        self._bot_admins = tuple(bot_admins)
        self._roles_service = roles_service
        self._journal: deque[dict[str, Any]] = deque(maxlen=_JOURNAL_MAX)
        self._console_ids: dict[str, str] = {}
        self._current_message: Any = None
        """The message being dispatched (beta-weekend-v2): the beta
        start panel needs the triggering message's channel to send
        its interactive view."""

    # -- wiring ------------------------------------------------------------

    @property
    def _debug(self) -> Any:
        return getattr(self.bot, "kingdoms_debug_service", None)

    @property
    def _time(self) -> Any:
        return getattr(self.bot, "kingdoms_time_service", None)

    def _strings(self, guild: Any) -> dict[str, str]:
        locale = getattr(guild, "preferred_locale", None)
        return CONSOLE_STRINGS["fr" if locale is None or str(locale).startswith("fr") else "en"]

    # -- channel resolution ------------------------------------------------

    async def console_channel_id(self, message: Any) -> str | None:
        """Resolve the console salon id for a message's guild (id-based)."""
        guild_id = str(getattr(message.guild, "id", "") or "")
        if not guild_id:
            return None
        channel_service = getattr(self.bot, "channel_service", None)
        if channel_service is not None:
            try:
                channel = await channel_service.get_channel_for_category(guild_id, CONSOLE_CHANNEL_KEY)
                if channel is not None and str(channel.id).isdigit():
                    self._console_ids[guild_id] = str(channel.id)
                    return str(channel.id)
            except Exception:
                logger.warning("CONSOLE: channel-service resolution failed", exc_info=True)
        # Fallback: the slug (the name labels the creation only).
        name = str(getattr(message.channel, "name", "") or "")
        if name.replace(" ", "-").replace("🛠-", "").strip("-").casefold() == CONSOLE_CHANNEL_SLUG:
            channel_id = str(getattr(message.channel, "id", "") or "")
            if channel_id.isdigit():
                self._console_ids[guild_id] = channel_id
                return channel_id
        return None

    # -- guard -------------------------------------------------------------

    async def is_console_admin_member(self, guild: Any, member: Any) -> bool:
        """Whether one member may use the console (every surface, v2).

        admin-console-v2: the guard was extracted from the message
        listener so the panel buttons and the command modal check the
        CLICKING member through the very same rules.
        """
        if getattr(member, "bot", False):
            return False
        if str(getattr(member, "id", "")) in self._bot_admins:
            return True
        permissions = getattr(member, "guild_permissions", None)
        if permissions is not None and getattr(permissions, "administrator", False):
            return True
        roles_service = self._roles_service
        if roles_service is None or guild is None:
            return False
        try:
            role_id = await roles_service.resolve_admin_role(str(guild.id))
        except Exception:
            logger.warning("CONSOLE: admin role lookup failed — denying", exc_info=True)
            return False
        if not role_id or not str(role_id).isdigit():
            return False
        member_role_ids = {str(getattr(role, "id", "")) for role in getattr(member, "roles", [])}
        return str(role_id) in member_role_ids

    async def is_console_admin(self, message: Any) -> bool:
        """Whether the message's author may use the console (every message)."""
        return await self.is_console_admin_member(getattr(message, "guild", None), message.author)

    # -- entry point --------------------------------------------------------

    async def handle_message(self, message: Any) -> None:
        """Guard, parse, run, answer one message in the salon (on_message)."""
        if getattr(message, "guild", None) is None:
            return
        channel_id = await self.console_channel_id(message)
        if channel_id is None or str(getattr(message.channel, "id", "")) != channel_id:
            return
        if not await self.is_console_admin(message):
            content = str(getattr(message, "content", "") or "").strip()
            if content:
                await self._send(message, self._strings(message.guild)["denied"])
            return
        text = str(getattr(message, "content", "") or "").strip()
        if not text:
            return  # the intent is off or an empty message — nothing to parse
        reply = await self.dispatch(text, message)
        if reply:
            await self._send(message, reply)

    async def _send(self, message: Any, text: str) -> None:
        """Answer in the salon (never ephemeral), chunked to the limit."""
        try:
            for chunk in _chunk(text, 1900):
                await message.channel.send(chunk)
        except Exception:
            logger.warning("CONSOLE: answer failed", exc_info=True)

    # -- dispatch ------------------------------------------------------------

    async def dispatch(self, text: str, message: Any | None = None, actor: str | None = None) -> str:
        """Parse one console line and run its command (pure, testable).

        admin-console-v2: ``actor`` overrides the journal's author (the
        panel buttons pass the clicking member's id); ``message`` stays
        the salon message (the beta panel and `regles` ride its channel).
        """
        self._current_message = message
        parts = text.split()
        command = parts[0].casefold()
        args = parts[1:]
        strings = _console_strings(getattr(getattr(message, "guild", None), "preferred_locale", None))
        spec = COMMANDES.get(command)
        if spec is None:
            return strings["unknown"].format(command=command)
        reply: str
        try:
            reply = await spec.handler(self, args)
        except (DebugError, KingdomsModError) as error:
            reply = f"⚠️ {error}"
        except ValueError as error:
            reply = f"⚠️ {error} — usage : `{spec.usage}`"
        actor = actor or str(getattr(getattr(message, "author", None), "id", "console"))
        self._journal.append(
            {
                "command": text,
                "actor": actor,
                "at": datetime.now(tz=None).strftime("%d/%m %H:%M"),
                "ok": not reply.startswith("⚠️"),
                "reply": _oneline(reply)[:120],
            }
        )
        return reply

    # -- journal ---------------------------------------------------------------

    def journal(self, count: int = 10) -> list[dict[str, Any]]:
        """Return the last ``count`` executed mutations (process-local)."""
        entries = list(self._journal)[-max(1, count) :]
        entries.reverse()
        return entries

    @property
    def current_message(self) -> Any:
        """Return the message of the dispatch in flight (None outside)."""
        return self._current_message


def _chunk(text: str, limit: int) -> list[str]:
    """Split a long console answer into sendable chunks (line-aware)."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _oneline(text: str) -> str:
    """Flatten a reply to one journal line (newlines → ' · ')."""
    return " · ".join(part.strip() for part in text.split("\n") if part.strip())


# ----------------------------------------------------------------------
# The handlers — each returns the reply string
# ----------------------------------------------------------------------


async def _cmd_help(console: AdminConsole, args: list[str]) -> str:
    """`aide [section]` — the full list or one section."""
    strings = CONSOLE_STRINGS["fr"]
    section = args[0].casefold() if args else None
    sections: dict[str, list[str]] = {}
    for spec in COMMANDES.values():
        sections.setdefault(spec.section, []).append(strings["help_line"].format(usage=spec.usage, help=spec.help_fr))
    if section is not None:
        if section not in sections:
            return strings["aide_sections"].format(sections=", ".join(sections))
        return strings["help_header"].format(section=section) + "\n" + "\n".join(sections[section])
    return (
        strings["help_header"].format(section="tout")
        + "\n"
        + "\n".join("\n".join(lines) for lines in sections.values())
    )


async def _cmd_etat(console: AdminConsole, args: list[str]) -> str:
    """`etat` — bypass, saison, cycle, âge, vols corbeau en cours."""
    del args
    debug = console._debug
    if debug is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    report = await debug.status()
    lines = [CONSOLE_STRINGS["fr"]["bypass_read"].format(state="1" if report.get("bypass") else "0")]
    time = console._time
    if time is not None:
        status = await time.status()
        if status.get("season") is None:
            lines.append(CONSOLE_STRINGS["fr"]["no_season"])
        else:
            lines.append(
                "saison : {season} · cycle {cycle}/{weeks} · âge {age} · phase {phase}".format(
                    season=status["season"],
                    cycle=status.get("cycle"),
                    weeks=status.get("weeks"),
                    age=status.get("age_display") or status.get("age"),
                    phase=status.get("phase"),
                )
            )
    thefts = report.get("thefts") or []
    lines.append("vols corbeau : " + (", ".join(f"{t['kingdom']} ({t['state']})" for t in thefts) if thefts else "—"))
    return "\n".join(lines)


async def _cmd_saison(console: AdminConsole, args: list[str]) -> str:
    """`saison` — saison, cycle, âge, semaine, phase."""
    del args
    time = console._time
    if time is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    status = await time.status()
    if status.get("season") is None:
        return CONSOLE_STRINGS["fr"]["no_season"]
    lines = [
        "saison : {season} · phase {phase} · cycle {cycle}/{weeks} · âge {age}".format(
            season=status["season"],
            phase=status.get("phase"),
            cycle=status.get("cycle"),
            weeks=status.get("weeks"),
            age=status.get("age_display") or status.get("age"),
        )
    ]
    for label, key in (
        ("prochain cycle (JdS)", "next_cycle_at"),
        ("prochain âge", "next_age_at"),
        ("fin de saison", "season_end_at"),
    ):
        slot = status.get(key)
        if slot is not None:
            lines.append(f"{label} : {slot.strftime('%d/%m %H:%M')}")
    return "\n".join(lines)


async def _cmd_voir(console: AdminConsole, args: list[str]) -> str:
    """`voir <royaume|seigneur>` — the full fiche."""
    if not args:
        raise ValueError("missing target")
    debug = console._debug
    target = " ".join(args)
    try:
        report = await debug.view_kingdom(target)
    except DebugError:
        report = await debug.view_lord(target)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_journal(console: AdminConsole, args: list[str]) -> str:
    """`journal [N]` — the last N executed mutations."""
    count = int(args[0]) if args else 10
    entries = console.journal(count)
    if not entries:
        return CONSOLE_STRINGS["fr"]["empty"]
    lines = [CONSOLE_STRINGS["fr"]["journal_header"].format(count=len(entries))]
    for entry in entries:
        lines.append(
            CONSOLE_STRINGS["fr"]["journal_line"].format(
                at=entry["at"],
                command=entry["command"],
                actor=entry["actor"],
                status="✅" if entry["ok"] else "⚠️",
                report=entry["reply"],
            )
        )
    return "\n".join(lines)


async def _cmd_cooldowns(console: AdminConsole, args: list[str]) -> str:
    """`cooldowns [royaume]` — the aggregated CD panel."""
    debug = console._debug
    report = await debug.view_cooldowns(args[0] if args else None)
    lines = []
    for panel in report.get("kingdoms", []):
        birds = ", ".join(f"{bird} ({_remaining(left)})" for bird, left in panel.get("birds", {}).items())
        lines.append(
            "**{name}** — oiseaux : {birds} · traquenard : {t}".format(
                name=panel["kingdom"],
                birds=birds or "—",
                t=_remaining(panel.get("traquenard_left", 0)) if panel.get("traquenard_left") else "—",
            )
        )
        for lock in panel.get("locks", []):
            lines.append(f"  · 🔒 {lock['lord']} ({lock['lock']} : {_remaining(lock['left'])})")
        for attack in panel.get("attacks", []):
            lines.append(
                "  · ⚔️ {id} {map} ({state}, échéance dans {left})".format(
                    id=attack["id"],
                    map=attack["map"],
                    state=attack["state"],
                    left=_remaining(attack["left"]),
                )
            )
    return "\n".join(lines) if lines else CONSOLE_STRINGS["fr"]["empty"]


async def _cmd_bypass(console: AdminConsole, args: list[str]) -> str:
    """`bypass [0|1]` — read or flip the ADMIN BYPASS (style cvar)."""
    strings = CONSOLE_STRINGS["fr"]
    if not args:
        return strings["bypass_read"].format(state="1" if admin_bypass_enabled() else "0")
    state = args[0]
    if state not in ("0", "1"):
        raise ValueError("bypass takes 0 or 1")
    enabled = state == "1"
    set_admin_bypass(enabled, source="console")
    return strings["bypass_on"] if enabled else strings["bypass_off"]


async def _cmd_fin_oiseau(console: AdminConsole, args: list[str]) -> str:
    """`fin_oiseau <royaume> [corbeau|faucon|pigeon|tous]` — CD à zéro."""
    if len(args) < 1:
        raise ValueError("kingdom required")
    debug = console._debug
    kingdom = args[0]
    bird = args[1].casefold() if len(args) > 1 else "tous"
    if bird not in BIRDS and bird not in ("tous", "all"):
        raise ValueError("unknown bird")
    report = await debug.reset_voliere_cooldown(kingdom, "all" if bird in ("tous", "all") else bird)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_ajouter_oiseau(console: AdminConsole, args: list[str]) -> str:
    """`ajouter_oiseau <royaume> <corbeau|faucon|pigeon> [qté]`."""
    if len(args) < 2:
        raise ValueError("kingdom and bird required")
    debug = console._debug
    kingdom, bird = args[0], args[1].casefold()
    if bird not in BIRDS:
        raise ValueError("unknown bird")
    count = max(1, int(args[2])) if len(args) > 2 else 1
    report = None
    for _ in range(count):
        report = await debug.grant_bird(kingdom, bird)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_fin_verrou(console: AdminConsole, args: list[str]) -> str:
    """`fin_verrou <seigneur> [marriage|exploration|ffa|tous]`."""
    if len(args) < 1:
        raise ValueError("lord required")
    debug = console._debug
    lock = args[1].casefold() if len(args) > 1 else "tous"
    if lock not in LOCK_KINDS and lock not in ("tous", "all"):
        raise ValueError("unknown lock")
    report = await debug.clear_lock(args[0], "all" if lock in ("tous", "all") else lock)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_fin_attaque(console: AdminConsole, args: list[str]) -> str:
    """`fin_attaque <id>` — force the NEXT phase (repeat to advance)."""
    if len(args) < 1:
        raise ValueError("attack id required")
    debug = console._debug
    report = await debug.force_next_phase(args[0])
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_voir_attaques(console: AdminConsole, args: list[str]) -> str:
    """`voir_attaques` — the ongoing attacks (id, phase, deadline)."""
    del args
    debug = console._debug
    running = await debug.list_attacks()
    if not running:
        return CONSOLE_STRINGS["fr"]["empty"]
    lines = []
    for attack in running:
        # attaques-phases-v2: the named phase (Siège/Draft/RDV/Score) —
        # the T-15/T-5/T clock stays internal (Drasah 15/10).
        phase = PHASES_FR.get(str(attack.get("phase")), str(attack.get("state")))
        lines.append(
            "⚔️ {id} · {map} · {phase} · échéance {deadline} (dans {left})".format(
                id=attack["id"],
                map=attack["map"],
                phase=phase,
                deadline=attack["expires_at"].strftime("%d/%m %H:%M"),
                left=_remaining(attack["expires_at"] - datetime.now(tz=attack["expires_at"].tzinfo)),
            )
        )
    return "\n".join(lines)


async def _cmd_resultat(console: AdminConsole, args: list[str]) -> str:
    """`resultat <id> <royaume|double>` — the admin face-check."""
    if len(args) < 2:
        raise ValueError("attack id and kingdom required")
    debug = console._debug
    report = await debug.admin_result(args[0], " ".join(args[1:]))
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_restituer(console: AdminConsole, args: list[str]) -> str:
    """`restituer <seigneur>` — give the weekly attack budget back (D17)."""
    if len(args) < 1:
        raise ValueError("lord required")
    debug = console._debug
    report = await debug.restitute_budget(" ".join(args))
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_ajouter_banque(console: AdminConsole, args: list[str]) -> str:
    """`ajouter_banque <royaume> <qté>` — +N/-N ⚙️."""
    if len(args) < 2:
        raise ValueError("kingdom and amount required")
    debug = console._debug
    report = await debug.adjust_bank(args[0], int(args[1]))
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_ajouter_mariage(console: AdminConsole, args: list[str]) -> str:
    """`ajouter_mariage <royaume> <qté> [civ…]` — N marriages in stock."""
    if len(args) < 2:
        raise ValueError("kingdom and count required")
    debug = console._debug
    civs = _extract_civs(args[2:])
    report = await debug.grant_marriages(args[0], int(args[1]), civs)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


def _extract_civs(raw: list[str]) -> list[str]:
    """Extract the trailing k=v pairs of a command as the optional civs."""
    civs: list[str] = []
    for chunk in raw:
        match = _PARAMS_RE.match(chunk.strip())
        civs.append(match.group(2).strip() if match else chunk)
    return civs


async def _cmd_ajouter_market(console: AdminConsole, args: list[str]) -> str:
    """`ajouter_market <royaume> <entité> <qté>` — forced market credit."""
    if len(args) < 2:
        raise ValueError("kingdom and entity required")
    debug = console._debug
    qty = int(args[2]) if len(args) > 2 else 1
    report = await debug.credit_market(args[0], args[1], qty)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_donner_civ(console: AdminConsole, args: list[str]) -> str:
    """`donner_civ <royaume> <civ>` — force a civ into the hand."""
    if len(args) < 2:
        raise ValueError("kingdom and civ required")
    debug = console._debug
    report = await debug.grant_civ(args[0], args[1])
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_avancer_vol(console: AdminConsole, args: list[str]) -> str:
    """`avancer_vol <royaume> <execution|fenetre>` — theft clock."""
    if len(args) < 2:
        raise ValueError("kingdom and stage required")
    debug = console._debug
    stage = args[1].casefold()
    if stage not in ("execute", "fenetre", "window"):
        raise ValueError("stage is execution or fenetre")
    report = await debug.advance_theft(args[0], "window" if stage in ("fenetre", "window") else "execute")
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_voir_vol(console: AdminConsole, args: list[str]) -> str:
    """`voir_vol <royaume>` — the theft state."""
    if len(args) < 1:
        raise ValueError("kingdom required")
    debug = console._debug
    report = await debug.view_theft(args[0])
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_donner_territoire(console: AdminConsole, args: list[str]) -> str:
    """`donner_territoire <map_key> <royaume>` — direct transfer."""
    if len(args) < 2:
        raise ValueError("map key and kingdom required")
    debug = console._debug
    report = await debug.grant_territory(args[0], args[1])
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_ajouter_temps(console: AdminConsole, args: list[str]) -> str:
    """`ajouter_temps <heures>-<minutes>` — advance the game clock."""
    if len(args) < 1:
        raise ValueError("duration required")
    match = _TIME_RE.match(args[0])
    if match is None:
        raise ValueError("format heures-minutes (ex. 2-30)")
    hours, minutes = int(match.group(1)), int(match.group(2))
    time = console._time
    if time is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    seconds = hours * 3600 + minutes * 60
    if seconds <= 0:
        raise ValueError("the jump needs a positive duration")
    report = await time.advance_seconds(seconds, label=f"console ajouter_temps {args[0]}")
    return CONSOLE_STRINGS["fr"]["done"].format(
        report=_fmt_report(
            {
                "jump": args[0],
                "offset": report.get("offset"),
                "target": report.get("target"),
            }
        )
    )


async def _cmd_skippe(console: AdminConsole, args: list[str]) -> str:
    """`skippe` — jump to the next scheduled event passage."""
    del args
    debug = console._debug
    report = await debug.skip_to_next_event()
    lines = [CONSOLE_STRINGS["fr"]["done"].format(report=f"event={report['event']} target={_fmt(report['target'])}")]
    reports = report.get("reports") or []
    ran = (
        ", ".join(str(entry) for entry in reports)
        if not isinstance(reports, dict)
        else ", ".join(f"{key}={value}" for key, value in reports.items())
    )
    if ran:
        lines.append(f"exécutés : {ran}")
    return "\n".join(lines)


async def _cmd_chronique(console: AdminConsole, args: list[str]) -> str:
    """`chronique <seigneur> <type> [k=v,…]` — inject a fake entry."""
    if len(args) < 2:
        raise ValueError("lord and kind required")
    debug = console._debug
    params: dict[str, str] = {}
    for chunk in " ".join(args[2:]).split(","):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            params[key.strip()] = value.strip()
    report = await debug.inject_chronicle(args[0], args[1], params)
    return CONSOLE_STRINGS["fr"]["done"].format(report=_fmt_report(report))


async def _cmd_lancer_beta(console: AdminConsole, args: list[str]) -> str:
    """`lancer_beta now [JJ/MM HH:MM]` — the beta start interface.

    beta-weekend-v2 (Drasah 17/10): the bare command answers with the
    interactive panel (🚀 Maintenant / 🕒 Programmer… — the modal
    captures the EXACT start). A direct date argument stays accepted
    (`lancer_beta now 23/10 20:00`) for the tests and the textual
    workflow: the plan rides that precise T0.
    """
    from kingdoms.mods.kingdoms.beta_panel import beta_start_panel, beta_start_report
    from kingdoms.mods.kingdoms.beta_weekend import parse_beta_start
    from kingdoms.mods.kingdoms.game_clock import game_now

    events = getattr(console.bot, "kingdoms_events_service", None)
    if events is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    if args and args[0].casefold() not in ("now", "maintenant"):
        return "usage : `lancer_beta now [JJ/MM HH:MM]` — ex. `lancer_beta now 23/10 20:00`"
    date_text = " ".join(args[1:]) if len(args) > 1 else ""
    if date_text:
        start = parse_beta_start(date_text, game_now())
        report = await events.activate_beta("now", start_at=start)
        return beta_start_report(report)
    # The interactive panel rides the triggering message's channel.
    message = console.current_message
    if message is None:
        return "usage : `lancer_beta now JJ/MM HH:MM` (ex. `23/10 20:00`) — l'interface nécessite le salon"
    content, view = beta_start_panel(console, None)
    try:
        await message.channel.send(content, view=view)
    except Exception:
        logger.warning("CONSOLE: beta panel send failed", exc_info=True)
        return "usage : `lancer_beta now JJ/MM HH:MM` (ex. `23/10 20:00`)"
    return "🚀 Panneau de départ beta envoyé — utilise les boutons ci-dessus."


async def _cmd_beta(console: AdminConsole, args: list[str]) -> str:
    """`beta` — the beta weekend status (preset, plan, next slot)."""
    kingdoms = getattr(console.bot, "kingdoms_service", None)
    if kingdoms is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    season = await kingdoms.current_season()
    if season is None:
        return CONSOLE_STRINGS["fr"]["no_season"]
    if not season.beta_name:
        from kingdoms.mods.kingdoms.beta_weekend import beta_preset_names

        return "aucune beta active — presets disponibles : " + ", ".join(beta_preset_names())
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Paris")
    fmt = "%d/%m %H:%M"
    from kingdoms.mods.kingdoms.game_clock import game_now as _game_now

    armed = season.beta_start_at is not None and season.beta_start_at > _game_now()
    lines = [f"beta « {season.beta_name} » " + ("ARMÉE :" if armed else "active :")]
    if season.beta_start_at is not None:
        lines.append(f"• départ : {season.beta_start_at.astimezone(tz).strftime(fmt)}")
    if season.beta_age_at is not None:
        lines.append(f"• passage d'âge : {season.beta_age_at.astimezone(tz).strftime(fmt)}")
    for index, slot in enumerate(season.beta_cycle_slots, start=1):
        lines.append(f"• cycle {index + 1} (Jour du Seigneur) : {slot.astimezone(tz).strftime(fmt)}")
    if season.beta_end_at is not None:
        lines.append(f"• fin de saison : {season.beta_end_at.astimezone(tz).strftime(fmt)}")
    return "\n".join(lines)


async def _cmd_regles(console: AdminConsole, args: list[str]) -> str:
    """`regles` — rebuild the guide threads of the Règles forum.

    guide-regles-v1 (Drasah 17/10/2026): after a salon reset, one
    command re-creates (or refreshes) every guide thread with the LIVE
    config values — the guide never lies. The refresh also runs at
    every deploy; the command exists for the manual re-trigger.
    """
    kingdoms = getattr(console.bot, "kingdoms_service", None)
    if kingdoms is None:
        return CONSOLE_STRINGS["fr"]["not_wired"]
    guild = getattr(console.current_message, "guild", None)
    if guild is None:
        return "usage : `regles` — la commande nécessite le salon (elle agit sur ce serveur)."
    from kingdoms.mods.kingdoms.kingdom_guide import refresh_guide_forum

    report = await refresh_guide_forum(guild, kingdoms)
    if not report:
        return "aucun forum « Règles » trouvé — crée le salon forum puis relance `regles`."
    created = sum(1 for status in report.values() if status == "created")
    updated = sum(1 for status in report.values() if status == "updated")
    failed = sum(1 for status in report.values() if status == "failed")
    lines = [f"📖 Guide du forum Règles : {created} créé(s), {updated} mis à jour."]
    if failed:
        lines.append(f"⚠️ {failed} échec(s) — voir les logs.")
    return "\n".join(lines)


COMMANDES: dict[str, CommandSpec] = {
    "aide": CommandSpec(
        "aide [section]",
        "La liste complète (ou une section)",
        "The full list (or one section)",
        "général",
        _cmd_help,
    ),
    "etat": CommandSpec(
        "etat",
        "Bypass, saison, cycle, âge, vols corbeau en cours",
        "Bypass, season, cycle, age, running corbeau thefts",
        "général",
        _cmd_etat,
    ),
    "saison": CommandSpec(
        "saison",
        "Saison, cycle, âge, semaine, phase",
        "Season, cycle, age, week, phase",
        "général",
        _cmd_saison,
    ),
    "voir": CommandSpec(
        "voir <royaume|seigneur>",
        "Fiche complète (banque, oiseaux, verrous, palier, civs)",
        "The full fiche (bank, birds, locks, tier, civs)",
        "général",
        _cmd_voir,
    ),
    "journal": CommandSpec(
        "journal [N]",
        "Les N dernières mutations exécutées",
        "The last N executed mutations",
        "général",
        _cmd_journal,
    ),
    "cooldowns": CommandSpec(
        "cooldowns [royaume]",
        "TOUS les CD actifs : oiseaux, verrous, attaques",
        "EVERY active CD: birds, locks, attacks",
        "général",
        _cmd_cooldowns,
    ),
    "bypass": CommandSpec(
        "bypass [0|1]",
        "Lire / activer / désactiver (gates des VRAIS boutons joueurs)",
        "Read / toggle (the REAL player-button gates)",
        "bypass",
        _cmd_bypass,
    ),
    "fin_oiseau": CommandSpec(
        "fin_oiseau <royaume> [corbeau|faucon|pigeon|tous]",
        "CD volière à zéro (effets applicables, tirage naturel)",
        "Volière CD to zero (effects testable, natural draw)",
        "volière",
        _cmd_fin_oiseau,
    ),
    "ajouter_oiseau": CommandSpec(
        "ajouter_oiseau <royaume> <corbeau|faucon|pigeon> [qté]",
        "Rendre son perchoir à un oiseau (quantité = répéter)",
        "Restore a bird to its slot (quantity = repeat)",
        "volière",
        _cmd_ajouter_oiseau,
    ),
    "fin_verrou": CommandSpec(
        "fin_verrou <seigneur> [marriage|exploration|ffa|tous]",
        "Purger un verrou temps réel",
        "Purge a real-time lock",
        "verrous",
        _cmd_fin_verrou,
    ),
    "fin_attaque": CommandSpec(
        "fin_attaque <id>",
        "Forcer la phase suivante (1 appel = 1 cran, jusqu'au no-show)",
        "Force the next phase (1 call = 1 step, up to the no-show line)",
        "attaques",
        _cmd_fin_attaque,
    ),
    "voir_attaques": CommandSpec(
        "voir_attaques",
        "Les attaques en cours (id, phase, échéance — source des id)",
        "The ongoing attacks (id, phase, deadline — the id source)",
        "attaques",
        _cmd_voir_attaques,
    ),
    "resultat": CommandSpec(
        "resultat <id> <royaume|double>",
        "Face-check admin : override / arbitrage d'un résultat",
        "Admin face-check: result override / dispute arbitration",
        "attaques",
        _cmd_resultat,
    ),
    "restituer": CommandSpec(
        "restituer <seigneur>",
        "Rendre le budget d'attaque hebdo (D17)",
        "Give the weekly attack budget back (D17)",
        "attaques",
        _cmd_restituer,
    ),
    "ajouter_banque": CommandSpec(
        "ajouter_banque <royaume> <qté>",
        "+N/-N ⚙️ en banque tech",
        "+N/-N ⚙️ on the tech bank",
        "économie",
        _cmd_ajouter_banque,
    ),
    "ajouter_mariage": CommandSpec(
        "ajouter_mariage <royaume> <qté> [civ…]",
        "N mariages dans les stocks (civs optionnelles)",
        "N marriages in stock (civs optional)",
        "économie",
        _cmd_ajouter_mariage,
    ),
    "ajouter_market": CommandSpec(
        "ajouter_market <royaume> <entité> <qté>",
        "Créditer Explorateur/Garde/Traquenard/Patrouille/Corruption…",
        "Credit Explorateur/Garde/Traquenard/Patrouille/Corruption…",
        "économie",
        _cmd_ajouter_market,
    ),
    "donner_civ": CommandSpec(
        "donner_civ <royaume> <civ>",
        "Forcer une civ dans la main",
        "Force a civ into the hand",
        "économie",
        _cmd_donner_civ,
    ),
    "avancer_vol": CommandSpec(
        "avancer_vol <royaume> <execution|fenetre>",
        "Accélérer l'horloge du vol corbeau",
        "Fast-forward the corbeau theft clock",
        "corbeau",
        _cmd_avancer_vol,
    ),
    "voir_vol": CommandSpec(
        "voir_vol <royaume>",
        "État du vol : phase, ⚙️ volés, fenêtre",
        "The theft state: phase, stolen ⚙️, window",
        "corbeau",
        _cmd_voir_vol,
    ),
    "donner_territoire": CommandSpec(
        "donner_territoire <map_key> <royaume>",
        "Transférer un territoire",
        "Transfer a territory",
        "territoires",
        _cmd_donner_territoire,
    ),
    "ajouter_temps": CommandSpec(
        "ajouter_temps <heures>-<minutes>",
        "Avancer l'horloge de jeu (Machine à temps, raccourci)",
        "Advance the game clock (time machine shortcut)",
        "territoires",
        _cmd_ajouter_temps,
    ),
    "skippe": CommandSpec(
        "skippe",
        "Sauter au prochain passage d'événement (cycle/JdS, âge, fin)",
        "Skip to the next event passage (cycle/JdS, age, end)",
        "territoires",
        _cmd_skippe,
    ),
    "chronique": CommandSpec(
        "chronique <seigneur> <type> [k=v,…]",
        "Injecter une entrée factice de chronique",
        "Inject a fake chronicle entry",
        "chronique",
        _cmd_chronique,
    ),
    "lancer_beta": CommandSpec(
        "lancer_beta now [JJ/MM HH:MM]",
        "Interface de départ beta (boutons) ou départ direct — plan 52 h",
        "Beta start interface (buttons) or direct start — 52 h plan",
        "saison",
        _cmd_lancer_beta,
    ),
    "beta": CommandSpec(
        "beta",
        "État de la beta (preset, plan, prochaine étape)",
        "Beta status (preset, plan, next step)",
        "saison",
        _cmd_beta,
    ),
    "regles": CommandSpec(
        "regles",
        "Redéployer les posts du guide dans le forum Règles (valeurs live)",
        "Rebuild the guide threads of the Règles forum (live values)",
        "contenu",
        _cmd_regles,
    ),
}


# ----------------------------------------------------------------------
# The interactive pinned panel (admin-console-v2, Drasah 17/10/2026)
# ----------------------------------------------------------------------


async def _console_reply(interaction: Any, text: str) -> None:
    """Answer a button/modal click IN THE SALON (never ephemeral, chunked)."""
    chunks = _chunk(text, 1900)
    try:
        await interaction.response.send_message(chunks[0])
        for extra in chunks[1:]:
            await interaction.followup.send(extra)
    except Exception:
        logger.warning("CONSOLE: interaction answer failed", exc_info=True)


def _console_view(locale: Any = None, console: "AdminConsole | None" = None) -> Any:
    """Build the pinned interactive console panel (read buttons + modal).

    admin-console-v2: the buttons answer in the salon through the very
    same ``dispatch``/``COMMANDES`` table — zero new command logic. The
    ⌨️ Commande… modal captures any free-text line. The console resolves
    from the module singleton at click time (restart-proof, §3b style);
    ``console`` forces an instance for the tests. The classes live
    INSIDE this factory on purpose: they are not module attributes, so
    the module's pydoc page stays free of discord-inherited sections.
    """
    strings = _console_strings(locale)

    class _CommandModal(discord.ui.Modal):
        """The free-text command window — the parser stays the table."""

        def __init__(self) -> None:
            super().__init__(title=strings["modal_title"][:45], timeout=300)
            self.command_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
                label=strings["modal_label"][:45],
                placeholder=strings["modal_placeholder"][:100],
                required=True,
                max_length=200,
            )
            self.add_item(self.command_input)

        async def on_submit(self, interaction: discord.Interaction) -> None:
            """Run the typed line and answer in the salon (the history)."""
            live = console or _current_console()
            if live is None:
                await interaction.response.send_message(strings["not_wired"], ephemeral=True)
                return
            if not await live.is_console_admin_member(interaction.guild, interaction.user):
                await interaction.response.send_message(strings["denied"], ephemeral=True)
                return
            text = str(getattr(self.command_input, "value", "") or "").strip()
            if not text:
                await interaction.response.send_message(strings["cmd_empty"], ephemeral=True)
                return
            actor = str(getattr(interaction.user, "id", "console"))
            reply = await live.dispatch(text, getattr(interaction, "message", None), actor=actor)
            await _console_reply(interaction, reply)

    class _View(discord.ui.View):
        """The pinned console panel rows (timeout=None, custom ids)."""

        def __init__(self) -> None:
            super().__init__(timeout=None)

        async def _run(self, interaction: discord.Interaction, command: str) -> None:
            """Guard then run one command line, answering in the salon."""
            live = console or _current_console()
            if live is None:
                await interaction.response.send_message(strings["not_wired"], ephemeral=True)
                return
            if not await live.is_console_admin_member(interaction.guild, interaction.user):
                await interaction.response.send_message(strings["denied"], ephemeral=True)
                return
            actor = str(getattr(interaction.user, "id", "console"))
            reply = await live.dispatch(command, getattr(interaction, "message", None), actor=actor)
            await _console_reply(interaction, reply)

        def _make_on_read(self, command: str) -> Any:
            """Bind one read-only command to its button."""

            async def _on_read(interaction: discord.Interaction) -> None:
                await self._run(interaction, command)

            return _on_read

        async def _on_bypass(self, interaction: discord.Interaction) -> None:
            """Flip the ADMIN BYPASS the comfortable way (toggle)."""
            target = "0" if admin_bypass_enabled() else "1"
            await self._run(interaction, f"bypass {target}")

    grey = discord.ButtonStyle.secondary
    view = _View()
    read_rows: list[tuple[int, str, str, Any]] = [
        (0, strings["btn_aide"], "kingdoms:console:aide", "aide"),
        (0, strings["btn_etat"], "kingdoms:console:etat", "etat"),
        (0, strings["btn_saison"], "kingdoms:console:saison", "saison"),
        (0, strings["btn_cooldowns"], "kingdoms:console:cooldowns", "cooldowns"),
        (0, strings["btn_attaques"], "kingdoms:console:attaques", "voir_attaques"),
        (1, strings["btn_journal"], "kingdoms:console:journal", "journal"),
        (1, strings["btn_beta"], "kingdoms:console:beta", "lancer_beta now"),
    ]
    buttons: list[tuple[int, str, str, Any, Any]] = [
        (row, label, custom_id, grey, view._make_on_read(command))
        for row, label, custom_id, command in read_rows
    ]
    buttons.append((1, strings["btn_bypass"], "kingdoms:console:bypass", discord.ButtonStyle.danger, view._on_bypass))
    buttons.append((1, strings["btn_command"], "kingdoms:console:command", discord.ButtonStyle.primary, None))
    for row, label, custom_id, style, handler in buttons:
        button: discord.ui.Button[discord.ui.View] = discord.ui.Button(
            label=label[:80], custom_id=custom_id, style=style, row=row
        )
        if handler is not None:
            button.callback = handler  # type: ignore[method-assign]
        else:
            # The ⌨️ Commande… button opens the modal instead of running.
            async def _on_command(interaction: discord.Interaction) -> None:
                await interaction.response.send_modal(_CommandModal())

            button.callback = _on_command  # type: ignore[method-assign]
        view.add_item(button)
    return view


async def ensure_console_panel(channel: Any) -> bool:
    """Pin the interactive console panel when absent (idempotent, best effort).

    The panel message is EDITED IN PLACE at every deploy with a fresh
    view — the buttons stay alive after every restart (the register also
    pre-attaches the view for the custom ids, §3b style).
    """
    strings = _console_strings(getattr(getattr(channel, "guild", None), "preferred_locale", None))
    content = strings["panel_body"]
    view = _console_view()
    try:
        for pinned in await channel.pins():
            if CONSOLE_PANEL_MARKER in str(getattr(pinned, "content", "")):
                try:
                    await pinned.edit(content=content, view=view)
                except Exception:
                    logger.warning("CONSOLE: panel refresh failed", exc_info=True)
                return False
        message = await channel.send(content, view=view)
        await message.pin()
        return True
    except Exception:
        logger.warning("CONSOLE: panel pin failed", exc_info=True)
        return False


# ----------------------------------------------------------------------
# The pinned welcome message (best effort)
# ----------------------------------------------------------------------


async def ensure_console_welcome(channel: Any) -> bool:
    """Pin the welcome message when absent (idempotent, best effort)."""
    try:
        for pinned in await channel.pins():
            if CONSOLE_WELCOME_MARKER in str(getattr(pinned, "content", "")):
                return False
        message = await channel.send(CONSOLE_STRINGS["fr"]["welcome"])
        await message.pin()
        return True
    except Exception:
        logger.warning("CONSOLE: welcome pin failed", exc_info=True)
        return False


# ----------------------------------------------------------------------
# The bot registration — the listener
# ----------------------------------------------------------------------


def register_kingdoms_admin_console(bot: Any) -> None:
    """Attach the console listener onto the bot (register phase).

    The listener guards EVERY message: only the console salon, only
    bot admins; the answers go to the salon, never ephemeral.
    """
    status = getattr(bot, "status_service", None)
    console = AdminConsole(
        bot,
        bot_admins=tuple(getattr(status, "bot_admins", ())),
        roles_service=getattr(bot, "roles_service", None),
    )
    bot.kingdoms_admin_console = console
    global _CONSOLE
    _CONSOLE = console
    # admin-console-v2: pre-attach the persistent panel view so the pinned
    # message's custom ids answer from the first second after every restart
    # (the deploy edits the message with a fresh view on top of this).
    add_view = getattr(bot, "add_view", None)
    if callable(add_view):
        try:
            add_view(_console_view())
            logger.info("kingdoms admin console panel view registered (custom ids)")
        except Exception:
            logger.warning("kingdoms admin console: panel view registration failed", exc_info=True)

    async def on_kingdoms_console_message(message: Any) -> None:
        """Route one message through the console (guard inside)."""
        await console.handle_message(message)

    # Tolerant: the fake bots of the unit tests expose no listener seam.
    add_listener = getattr(bot, "add_listener", None)
    if callable(add_listener):
        add_listener(on_kingdoms_console_message, "on_message")
        logger.info("kingdoms admin console registered (listener on_message)")
    else:
        logger.warning("kingdoms admin console: the bot exposes no add_listener — listener skipped")
