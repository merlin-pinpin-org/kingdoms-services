"""Kingdoms mod — salons-first live content (kingdoms#138, group 1).

Best-effort refreshers that keep the designer's channels alive with
the T2 service only: the Époque channel carries the current age (the
channel itself is renamed on each age switch), the Seigneurs channel
carries the per-kingdom roster, the Géopolitique channel receives
the enrollment announcements, the Présentation announce channel pins
the designer's pitch of the mod, and the Taverne/Règles channels get
their designer descriptions.

Every refresher is marker-based (edit the marked message when present,
post otherwise) and never raises: a missing channel or service simply
skips the refresh, exactly like ``refresh_season_status``.

Designer-authored content (the mod pitch, the Taverne and Règles
descriptions) is French-only by decision — it is the designer's voice,
not the bot UI.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_content")

EPOCH_MARKER = "kingdoms:epoch:status"
LORDS_MARKER = "kingdoms:lords:roster"
PRESENTATION_MARKER = "kingdoms:presentation"
EPOCH_FALLBACK_CHANNEL = "Âge sombre"
GEOPOLITICS_CHANNEL = "Géopolitique"
LORDS_CHANNEL = "Seigneurs"
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
    "💍 Mariage · 🔬 Technologie · 🤝 Traité · 📋 Inscription & Royaumes · "
    "🗺️ Territoires & Cadastre · 🏆 Fin de saison · ⚖️ Fair-play & sanctions."
)

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "epoch_title": "🕰️ Current age",
        "epoch_progress": "Season — cycle {}/{}",
        "epoch_bonus_tech": "Tech points per cycle",
        "epoch_bonus_marriage": "Bonus marriages",
        "epoch_gaia_ai": "Gaïa AI level",
        "epoch_hint": "The age switches automatically on Wednesday at midnight.",
        "lords_title": "📜 Kingdoms roster",
        "lords_empty": "No kingdom yet — enrollments are open in Postuler.",
        "lords_king": "King",
        "lords_lords": "Lords",
        "lords_pending": "Waiting for a kingdom",
        "geo_founded": "🏛️ The kingdom **{}** has been founded by {} — long live the {}!",
        "geo_joined": "⚔️ {} has joined the kingdom **{}** as a Lord.",
        "geo_queued": "⏳ {} has entered the waiting queue.",
    },
    "fr": {
        "epoch_title": "🕰️ Époque actuelle",
        "epoch_progress": "Saison — cycle {}/{}",
        "epoch_bonus_tech": "Points de tech par cycle",
        "epoch_bonus_marriage": "Mariages bonus",
        "epoch_gaia_ai": "Niveau d'IA Gaïa",
        "epoch_hint": "L'époque bascule automatiquement le mercredi à minuit.",
        "lords_title": "📜 Effectifs des royaumes",
        "lords_empty": "Aucun royaume pour le moment — les inscriptions sont ouvertes dans Postuler.",
        "lords_king": "Roi",
        "lords_lords": "Seigneurs",
        "lords_pending": "En attente d'un royaume",
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
    """Return the epoch channel, whatever its current age name.

    The channel is renamed at each age switch (``Âge sombre``,
    ``Âge féodal``…), so a lookup by any single name would miss it.
    The stable anchor is the wired ChannelService's resolved category
    (``kingdoms:epoch`` — database-backed); the declared display name
    is the fallback for a freshly provisioned guild.
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


def _epoch_content(strings: dict[str, str], display: str, age: Any, cycle: int, weeks: int) -> str:
    """Build the pinned Époque message body for the current age."""
    lines = [
        f"# {strings['epoch_title']} : {display}",
    ]
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
    lines.extend([f"*{strings['epoch_hint']}*", EPOCH_MARKER])
    return "\n".join(lines)


async def refresh_epoch_channel(guild: discord.Guild, locale: str, kingdoms_service: Any) -> bool:
    """Rename the Époque channel to the current age and pin its bonuses."""
    if kingdoms_service is None:
        return False
    channel = await _epoch_channel(guild)
    if channel is None:
        return False
    try:
        age, display, season = await _current_age(kingdoms_service)
        content = _epoch_content(
            _strings(locale),
            display,
            age,
            season.current_cycle if season is not None else 0,
            season.weeks if season is not None else 0,
        )
        if _slug(channel.name) != _slug(display):
            await channel.edit(name=display, reason="kingdoms: rename to the current age")
        return await _upsert_marked(channel, content, EPOCH_MARKER)
    except Exception:
        logger.warning("KINGDOM CONTENT: epoch refresh skipped", exc_info=True)
        return False


def _roster_content(strings: dict[str, str], kingdoms: list[Any], lords: list[Any]) -> str:
    """Build the per-kingdom roster body (kings, lords, waiting queue)."""
    lines = [f"# {strings['lords_title']}"]
    active = [lord for lord in lords if not lord.left]
    named = [k for k in kingdoms if not k.is_gaia]
    if not named:
        lines.append(strings["lords_empty"])
    for kingdom in sorted(named, key=lambda k: k.name):
        members = [lord for lord in active if lord.kingdom_id == kingdom.id and not lord.in_queue]
        king = next((m for m in members if m.role.value == "king"), None)
        if king is not None:
            lines.append(f"\n## {kingdom.name}\n- 👑 {strings['lords_king']} : {king.display_name}")
        else:
            lines.append(f"\n## {kingdom.name}\n- 👑 {strings['lords_king']} : —")
        subjects = [m for m in members if m.role.value != "king"]
        if subjects:
            names = ", ".join(m.display_name for m in subjects)
            lines.append(f"- 🎖️ {strings['lords_lords']} : {names}")
    waiting = [lord for lord in active if lord.in_queue]
    if waiting:
        names = ", ".join(lord.display_name for lord in waiting)
        lines.append(f"\n⏳ {strings['lords_pending']} : {names}")
    lines.append(LORDS_MARKER)
    return "\n".join(lines)


async def refresh_lords_roster(guild: discord.Guild, locale: str, kingdoms_service: Any) -> bool:
    """Post (or refresh) the per-kingdom roster in the Seigneurs channel."""
    if kingdoms_service is None:
        return False
    channel = _find_channel(guild, LORDS_CHANNEL)
    if channel is None:
        return False
    try:
        kingdoms = await kingdoms_service.kingdoms()
        lords = await kingdoms_service.lords()
        content = _roster_content(_strings(locale), kingdoms, lords)
        return await _upsert_marked(channel, content, LORDS_MARKER)
    except Exception:
        logger.warning("KINGDOM CONTENT: lords roster refresh skipped", exc_info=True)
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


async def announce_geopolitics(guild: discord.Guild, locale: str, text: str) -> bool:
    """Send one announcement in the Géopolitique channel (best effort)."""
    channel = _find_channel(guild, GEOPOLITICS_CHANNEL)
    if channel is None:
        return False
    try:
        await channel.send(text)
        return True
    except Exception:
        logger.warning("KINGDOM CONTENT: geopolitics announcement failed", exc_info=True)
        return False


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
        if await refresh_lords_roster(guild, locale, kingdoms_service):
            report["seigneurs"] = "deployed"
    except Exception:
        logger.warning("KINGDOM CONTENT: lords deployment failed", exc_info=True)
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
