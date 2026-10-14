"""Kingdoms mod — salons-first live content (kingdoms#138, group 1).

Best-effort refreshers that keep the designer's channels alive with
the T2 service only: the fixed-name 🏺 Âge channel carries the current
age plus the next age-switch countdown, the Géopolitique channel
receives the enrollment announcements, the Présentation announce
channel pins the designer's pitch of the mod, and the Taverne/Règles
channels get their designer descriptions.

Every refresher is marker-based (edit the marked message when present,
post otherwise) and never raises: a missing channel or service simply
skips the refresh, exactly like ``refresh_season_status``.

Designer-authored content (the mod pitch, the Taverne and Règles
descriptions) is French-only by decision — it is the designer's voice,
not the bot UI.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import discord

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_content")

EPOCH_MARKER = "kingdoms:epoch:status"
PRESENTATION_MARKER = "kingdoms:presentation"
EPOCH_FALLBACK_CHANNEL = "🏺 Âge"
AGE_CHANNEL = "🏺 Âge"
KINGDOMS_CATEGORY = "Kingdoms"
GEOPOLITICS_CHANNEL = "Géopolitique"
PRESENTATION_CHANNEL = "Présentation"
TAVERNE_CHANNEL = "Taverne"
REGLES_FORUM = "Règles"

# The designer's pitch of the mod (form corrected, content alpha —
# kingdoms#138). Pinned in the Présentation announce channel.
PRESENTATION_MESSAGE = """# ⚔️ Kingdoms — Saison II

Kingdoms est un événement, un mod externe au jeu Age of Empires II !
Il s'agit du premier mod externe **conquête de territoire** d'Age of Empires II !

Plusieurs équipes qu'on nomme ici **Royaume** vont se confronter !

L'objectif de chaque Royaume sera de remporter un maximum de maps, appelées ici **territoires** !
Le Royaume qui possèdera le plus de territoires dans le temps imparti remportera la victoire !

**Comment fonctionne l'univers de Kingdoms ?**

Kingdoms se déroule sur un mois entier !

Chaque Royaume est doté de 5 territoires et de 5 civilisations qu'on nommera **Alliances**.
Il existe aussi le Royaume Gaïa, géré par une IA, possédant 8 territoires.
Chaque semaine, chaque joueur qu'on nommera ici **Seigneur** disposera d'une attaque et d'une défense !

Lorsque vous souhaitez attaquer, vous attaquez un territoire et non un seigneur !
"En fonction du territoire attaqué, si ce territoire appartient à un autre royaume, "
"il leur sera possible de le défendre !"
"S'il s'agit d'un territoire Gaïa, vous devrez affronter une IA sur le territoire attaqué ! "
"Attention, il est possible qu'un autre Seigneur se joigne à la bataille !"

Ici, dans la **Saison II** de Kingdoms, il n'est possible d'attaquer que du vendredi au dimanche !

"Sur la première semaine, vous débuterez à l'Âge sombre puis, chaque semaine, vous avancerez dans les âges "
"et vous débloquerez de nouveaux bonus et de nouvelles mécaniques !"

"Dans ce mod, il vous sera possible de fédérer des alliances ! De proposer des traités ! "
"D'organiser des mariages et bien plus encore !"

N'hésite pas, pour en savoir plus, à consulter les règles !
"""

TAVERNE_DESCRIPTION = "La taverne du serveur — discussions libres autour d'un chaudron."

REGLES_DESCRIPTION = (
    "Les règles complètes du mod Kingdoms — Saison II. À lire avant de s'inscrire.\n"
    "Sommaire : ⚔️ Attaque & Défense · 🕰️ Époque · 👑 Jour du Seigneur · 🧭 Exploration · "
    "💍 Mariage · ⚙️ Technologie · 🤝 Traité · 📋 Inscription & Royaumes · "
    "🗺️ Territoires & Cadastre · 🏆 Fin de saison · ⚖️ Fair-play & sanctions."
)

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "epoch_title": "🕰️ Current age",
        "age_next": "⏭️ Next age switch: Wednesday {day}/{month} at {hour}h{minute} (Paris)",
        "age_scale": "Age scale",
        "epoch_progress": "Season — cycle {}/{}",
        "epoch_bonus_tech": "Tech points per cycle",
        "epoch_bonus_marriage": "Bonus marriages",
        "epoch_gaia_ai": "Gaïa AI level",
        "epoch_hint": "The age switches automatically on Wednesday at midnight.",
        "lords_king": "King",
        "geo_founded": "🏛️ The kingdom **{}** has been founded by {} — long live the {}!",
        "geo_joined": "⚔️ {} has joined the kingdom **{}** as a Lord.",
        "geo_queued": "⏳ {} has entered the waiting queue.",
    },
    "fr": {
        "epoch_title": "🕰️ Époque actuelle",
        "age_next": "⏭️ Prochain passage d'âge : mercredi {day}/{month} à {hour}h{minute} (Paris)",
        "age_scale": "Échelle des âges",
        "epoch_progress": "Saison — cycle {}/{}",
        "epoch_bonus_tech": "Points de tech par cycle",
        "epoch_bonus_marriage": "Mariages bonus",
        "epoch_gaia_ai": "Niveau d'IA Gaïa",
        "epoch_hint": "L'époque bascule automatiquement le mercredi à minuit.",
        "lords_king": "Roi",
        "geo_founded": "🏛️ Le royaume **{}** a été fondé par {} — longue vie au {} !",
        "geo_joined": "⚔️ {} a rejoint le royaume **{}** en tant que Seigneur.",
        "geo_queued": "⏳ {} est entré dans la file d'attente.",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Resolve the catalog for one locale (French fallback, designer-authored pattern)."""
    return STRINGS["fr"] if str(locale).lower().startswith("fr") else STRINGS["en"]


def _find_channel(guild: discord.Guild, name: str) -> Any:
    """Find one text channel by slug, whatever its category."""
    wanted = _slug(name)
    for channel in guild.text_channels:
        if _slug(channel.name) == wanted:
            return channel
    return None


async def _epoch_channel(guild: discord.Guild) -> Any:
    """Return the fixed-name 🏺 Âge channel (kingdoms-patch restructuration-royaume).

    The channel keeps its declared key (``kingdoms:epoch`` — the DB
    resolution adopts the old « Âge sombre » channel), but its NAME is
    now FIXED: the current age lives in the pinned message, not in the
    channel name. The stable anchor is the wired ChannelService's
    resolution; the declared display name is the fallback.
    """
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring
    from kingdoms.mods.kingdoms.kingdom_setup import EPOCH_CHANNEL_KEY, MOD_NAME

    wiring = _wiring()
    if wiring.channel_service is not None:
        try:
            resolved_channel = await wiring.channel_service.get_channel_for_category(
                str(guild.id), f"{MOD_NAME}:{EPOCH_CHANNEL_KEY}"
            )
        except Exception:
            logger.warning("KINGDOM CONTENT: epoch channel lookup failed", exc_info=True)
            resolved_channel = None
        if resolved_channel is not None and resolved_channel.id.isdigit():
            channel = guild.get_channel(int(resolved_channel.id))
            if channel is not None:
                return channel
    return _find_channel(guild, EPOCH_FALLBACK_CHANNEL)


async def _upsert_marked(channel: Any, content: str, marker: str) -> bool:
    """Edit the marked message of one channel, or post it when missing."""
    from kingdoms.mods.kingdoms.panel_messages import channel_messages, message_text

    for message in await channel_messages(channel):
        if marker in message_text(message):
            try:
                await message.edit(content=content)
            except Exception:
                logger.warning("KINGDOM CONTENT: marked refresh failed", exc_info=True)
            return True
    await channel.send(content)
    return True


async def _current_age(service: Any) -> tuple[Any, str, Any]:
    """Read the age model, its display name and the running season."""
    config = service.config
    season = await service.current_season()
    age_key = season.current_age_key if season is not None else (config.ages[0].key if config.ages else "")
    age = next((entry for entry in config.ages if entry.key == age_key), None)
    display = age.display_name if age is not None else (EPOCH_FALLBACK_CHANNEL if not age_key else age_key)
    return age, display, season


def _next_age_switch(now: datetime) -> datetime:
    """Return the next Wednesday 00:00 (Europe/Paris) age switch."""
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    local = now.astimezone(paris)
    days_ahead = (2 - local.weekday()) % 7  # Wednesday is weekday 2
    candidate = (local + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate


def _age_scale(strings: dict[str, str], ages: list[Any], current_key: str) -> str:
    """Render the age scale (● current, ▸ the other ages)."""
    marks = {
        str(getattr(age, "key", "")): "●" if str(getattr(age, "key", "")) == str(current_key) else "▸"
        for age in ages
    }
    order = [k for k in marks if marks[k] == "●"] + [k for k in marks if marks[k] == "▸"]
    display = {str(getattr(age, "key", "")): str(getattr(age, "display_name", "")) for age in ages}
    parts = [f"{marks[key]} {display.get(key, key)}" for key in order if key in marks]
    return " ".join(parts)


def _epoch_content(
    strings: dict[str, str],
    display: str,
    age: Any,
    cycle: int,
    weeks: int,
    ages: list[Any],
    next_switch: datetime,
) -> str:
    """Build the pinned 🏺 Âge message body (age, countdown, scale)."""
    lines = [
        f"# {strings['epoch_title']} : {display}",
    ]
    lines.append(
        str(strings["age_next"]).format(
            day=f"{next_switch.day:02d}",
            month=f"{next_switch.month:02d}",
            hour=f"{next_switch.hour:02d}",
            minute=f"{next_switch.minute:02d}",
        )
    )
    if weeks:
        lines.append(f"**{strings['epoch_progress'].format(cycle, weeks)}**")
    if age is not None:
        lines.extend(
            [
                f"- {strings['epoch_bonus_tech']} : {age.tech_points}",
                f"- {strings['epoch_bonus_marriage']} : {age.extra_marriages}",
                f"- {strings['epoch_gaia_ai']} : {age.gaia_ai_level}/5",
            ]
        )
    if ages:
        current_key = str(getattr(age, "key", "")) if age is not None else ""
        lines.append(f"**{strings['age_scale']}** : {_age_scale(strings, ages, current_key)}")
    lines.extend([f"*{strings['epoch_hint']}*", EPOCH_MARKER])
    return "\n".join(lines)


async def refresh_epoch_channel(guild: discord.Guild, locale: str, kingdoms_service: Any) -> bool:
    """Pin the current age in the fixed-name 🏺 Âge salon (kingdoms-patch).

    The channel is NO LONGER renamed at each age switch (Drasah's
    restructuration-royaume decision): the name stays « 🏺 Âge », the
    current age, the Wednesday-midnight countdown and the age scale
    live in the pinned message. A legacy « Âge sombre » channel (or a
    channel stranded in the legacy Époque category) is migrated once:
    renamed and moved under Kingdoms.
    """
    if kingdoms_service is None:
        return False
    channel = await _epoch_channel(guild)
    if channel is None:
        return False
    try:
        age, display, season = await _current_age(kingdoms_service)
        ages = list(getattr(kingdoms_service.config, "ages", None) or [])
        content = _epoch_content(
            _strings(locale),
            display,
            age,
            season.current_cycle if season is not None else 0,
            season.weeks if season is not None else 0,
            ages,
            _next_age_switch(datetime.now(tz=UTC)),
        )
        if _slug(channel.name) != _slug(AGE_CHANNEL):
            await channel.edit(name=AGE_CHANNEL, reason="kingdoms: fixed Âge salon name")
        category = next(
            (c for c in getattr(guild, "categories", []) if _slug(getattr(c, "name", "")) == _slug(KINGDOMS_CATEGORY)),
            None,
        )
        if category is not None and getattr(channel, "category_id", None) != category.id:
            await channel.edit(category=category, reason="kingdoms: move Âge under Kingdoms")
        return await _upsert_marked(channel, content, EPOCH_MARKER)
    except Exception:
        logger.warning("KINGDOM CONTENT: epoch refresh skipped", exc_info=True)
        return False


async def refresh_presentation(guild: discord.Guild) -> bool:
    """Pin the designer's mod pitch in the Présentation channel."""
    channel = _find_channel(guild, PRESENTATION_CHANNEL)
    if channel is None:
        return False
    try:
        content = f"{PRESENTATION_MESSAGE}\n{PRESENTATION_MARKER}"
        return await _upsert_marked(channel, content, PRESENTATION_MARKER)
    except Exception:
        logger.warning("KINGDOM CONTENT: presentation refresh skipped", exc_info=True)
        return False


async def refresh_channel_descriptions(guild: discord.Guild) -> dict[str, str]:
    """Set the designer descriptions of the Taverne and Règles channels."""
    report: dict[str, str] = {}
    for name, target, description in (
        (TAVERNE_CHANNEL, "taverne", TAVERNE_DESCRIPTION),
        (REGLES_FORUM, "règles", REGLES_DESCRIPTION),
    ):
        pool = [*guild.text_channels, *getattr(guild, "forums", [])]
        channel = next((c for c in pool if _slug(c.name) == _slug(name)), None)
        if channel is None:
            continue
        if getattr(channel, "topic", None) == description:
            report[target] = "deployed"
            continue
        try:
            await channel.edit(topic=description, reason="kingdoms: designer channel description")
            report[target] = "deployed"
        except Exception:
            logger.warning("KINGDOM CONTENT: channel description failed", exc_info=True)
    return report


async def _announce_geopolitics_message(
    guild: discord.Guild, text: str, view: discord.ui.View | None = None
) -> discord.Message | None:
    """Send one Géopolitique announcement; return the live message.

    The royal-wedding announcement (Drasah 2026-10-12) attaches ONE
    public « En savoir plus » thread to the message — the caller
    needs the live message back, not a mere success bool. The FFA
    Exploration announcement (Drasah 2026-10-14) carries the
    persistent « Aller explorer » button through ``view``. Best
    effort: a missing channel or a refused send returns None and
    never raises.
    """
    channel = _find_channel(guild, GEOPOLITICS_CHANNEL)
    if channel is None:
        return None
    try:
        if view is None:
            return cast(discord.Message, await channel.send(text))
        return cast(discord.Message, await channel.send(text, view=view))
    except Exception:
        logger.warning("KINGDOM CONTENT: geopolitics announcement failed", exc_info=True)
        return None


async def announce_geopolitics(
    guild: discord.Guild,
    locale: str,
    text: str,
    view: discord.ui.View | None = None,
) -> bool:
    """Send one announcement in the Géopolitique channel (best effort)."""
    return await _announce_geopolitics_message(guild, text, view=view) is not None


async def announce_enrollment(
    guild: discord.Guild,
    locale: str,
    display_name: str,
    kingdom_name: str | None,
    is_king: bool,
) -> None:
    """Announce one validated enrollment in Géopolitique (best effort)."""
    strings = _strings(locale)
    if kingdom_name is None:
        await announce_geopolitics(guild, locale, strings["geo_queued"].format(display_name))
    elif is_king:
        await announce_geopolitics(
            guild, locale, strings["geo_founded"].format(kingdom_name, display_name, strings["lords_king"])
        )
    else:
        await announce_geopolitics(guild, locale, strings["geo_joined"].format(display_name, kingdom_name))


async def refresh_salons_content(guild: discord.Guild, locale: str, kingdoms_service: Any) -> dict[str, str]:
    """Refresh every group-1 channel content; return a deploy-style report."""
    report: dict[str, str] = {}
    try:
        if await refresh_epoch_channel(guild, locale, kingdoms_service):
            report["époque"] = "deployed"
    except Exception:
        logger.warning("KINGDOM CONTENT: epoch deployment failed", exc_info=True)
    try:
        if await refresh_presentation(guild):
            report["présentation"] = "deployed"
    except Exception:
        logger.warning("KINGDOM CONTENT: presentation deployment failed", exc_info=True)
    try:
        report.update(await refresh_channel_descriptions(guild))
    except Exception:
        logger.warning("KINGDOM CONTENT: channel descriptions deployment failed", exc_info=True)
    return report
