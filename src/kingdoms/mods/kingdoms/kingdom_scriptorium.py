"""Kingdoms mod — 📜 Scriptorium salon (patch restructuration-royaume).

The Scriptorium carries a pinned sommaire plus three restart-proof
persistent buttons (🗺️ Cadastre / 🤝 Diplomatie / 🛡️ Seigneurie). Every
rubric is an EPHEMERAL view (option A — public, any member may consult
it, only the clicker sees the answer) with the standard market TTL.

The rubrics are read-only views over the T2 services and the declared
config: the Cadastre lists the map catalog with its tags and textual
bonus (cadastre v1), the Diplomatie lists the civilizations with a
placeholder for Drasah's unlock conditions (document to come), and
the Seigneurie lists every lord per kingdom with the REMAINING
attack/defense budgets of the current cycle (Check 46 format).
"""

from __future__ import annotations

import logging
import re
from typing import Any, cast

import discord

from kingdoms.mods.kingdoms.kingdom_setup import _slug

logger = logging.getLogger("kingdoms.kingdom_scriptorium")

SCRIPTORIUM_MARKER = "kingdoms:scriptorium:summary"
SCRIPTORIUM_CHANNEL = "📜 Scriptorium"

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "summary_title": "# 📜 Scriptorium",
        "summary_line": "The archive of the realm — consult the rubrics below.",
        "topic_cadastre": "🗺️ Cadastre",
        "topic_diplomatie": "🤝 Diplomatie",
        "topic_seigneurie": "🛡️ Seigneurie",
        "cadastre_title": "# 🗺️ Cadastre — territories and their effects",
        "cadastre_empty": "The map catalog is empty.",
        "diplomatie_title": "# 🤝 Diplomatie — civilizations and their conditions",
        "diplomatie_empty": "No civilization declared yet.",
        "diplomatie_condition_map": "own the map {maps}",
        "diplomatie_condition_map_type": "own at least {count} map(s) of type {types}",
        "diplomatie_condition_min_maps": "own at least {count} map(s)",
        "diplomatie_condition_max_maps": "own at most {count} map(s)",
        "diplomatie_condition_civ": "hold the {civ} civilization",
        "diplomatie_condition_pattern": "belong to the {pattern} kingdom",
        "diplomatie_condition_tech": "hold at least {count} tech point(s)",
        "diplomatie_condition_marriages": "have at least {count} married lord(s)",
        "diplomatie_condition_civ_count": "hold at least {count} civilization(s)",
        "seigneurie_title": "# 🛡️ Seigneurie — lords and remaining budgets",
        "seigneurie_empty": "No kingdom yet — enrollments are open in Postuler.",
        "seigneurie_king": "King",
        "seigneurie_lords": "Lords",
        "seigneurie_pending": "Waiting for a kingdom",
        "unknown": "unknown",
    },
    "fr": {
        "summary_title": "# 📜 Scriptorium",
        "summary_line": "L'archive du royaume — consultez les rubriques ci-dessous.",
        "topic_cadastre": "🗺️ Cadastre",
        "topic_diplomatie": "🤝 Diplomatie",
        "topic_seigneurie": "🛡️ Seigneurie",
        "cadastre_title": "# 🗺️ Cadastre — territoires et leurs effets",
        "cadastre_empty": "Le catalogue de maps est vide.",
        "diplomatie_title": "# 🤝 Diplomatie — civilisations et leurs conditions",
        "diplomatie_empty": "Aucune civilisation déclarée pour le moment.",
        "diplomatie_condition_map": "posséder la map {maps}",
        "diplomatie_condition_map_type": "posséder au moins {count} map(s) de type {types}",
        "diplomatie_condition_min_maps": "posséder au moins {count} map(s)",
        "diplomatie_condition_max_maps": "posséder au plus {count} map(s)",
        "diplomatie_condition_civ": "posséder la civilisation {civ}",
        "diplomatie_condition_pattern": "appartenir au royaume {pattern}",
        "diplomatie_condition_tech": "posséder au moins {count} point(s) technologique(s)",
        "diplomatie_condition_marriages": "avoir au moins {count} seigneur(s) marié(s)",
        "diplomatie_condition_civ_count": "posséder au moins {count} civilisation(s)",
        "seigneurie_title": "# 🛡️ Seigneurie — seigneurs et budgets restants",
        "seigneurie_empty": "Aucun royaume pour le moment — les inscriptions sont ouvertes dans Postuler.",
        "seigneurie_king": "Roi",
        "seigneurie_lords": "Seigneurs",
        "seigneurie_pending": "En attente d'un royaume",
        "unknown": "inconnu",
    },
}


def _strings(locale: str) -> dict[str, str]:
    """Return the module strings for one locale (fr by default)."""
    return _STRINGS["fr" if str(locale).lower().startswith("fr") else "en"]


def _locale(interaction: discord.Interaction) -> str:
    """Extract the interaction locale (en fallback)."""
    value = str(getattr(interaction, "locale", "") or "")
    return value if value else "en"


# ----------------------------------------------------------------------
# The three rubrics
# ----------------------------------------------------------------------


def _cadastre_content(strings: dict[str, str], maps: list[Any]) -> str:
    """Build the Cadastre rubric: the map catalog, tags and textual bonus."""
    from kingdoms.mods.kingdoms.kingdom_attack_panel import _map_description

    lines = [strings["cadastre_title"], ""]
    if not maps:
        lines.append(strings["cadastre_empty"])
        return "\n".join(lines)
    for entry in maps:
        name = str(getattr(entry, "display_name", getattr(entry, "key", "?")))
        description = _map_description(entry, strings)
        lines.append(f"- **{name}**" + (f" — {description}" if description else ""))
    return "\n".join(lines)


def _diplomatie_condition_text(civ: Any, strings: dict[str, str], maps: list[Any]) -> str:
    """Render one civilization's official acquisition condition (V1).

    cadastre-diplomatie-v1 (Drasah 15/10): the Diplomatie is the
    SOURCE of truth for civ acquisition — the maps' cadastre bonus
    texts are display-only and may diverge. Map keys read as their
    display names.
    """
    by_key = {str(getattr(entry, "key", "")): str(getattr(entry, "display_name", "?")) for entry in maps}
    conditions: list[str] = []
    if getattr(civ, "requires_map_keys", None):
        names = ", ".join(by_key.get(key, key) for key in civ.requires_map_keys)
        conditions.append(strings["diplomatie_condition_map"].format(maps=names))
    if getattr(civ, "requires_map_types", None):
        conditions.append(
            strings["diplomatie_condition_map_type"].format(
                count=int(getattr(civ, "min_map_type_count", 1) or 1),
                types=", ".join(civ.requires_map_types),
            )
        )
    if getattr(civ, "min_map_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_min_maps"].format(count=int(civ.min_map_count))
        )
    if getattr(civ, "max_map_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_max_maps"].format(count=int(civ.max_map_count))
        )
    if getattr(civ, "requires_civilization", None):
        conditions.append(
            strings["diplomatie_condition_civ"].format(civ=str(civ.requires_civilization))
        )
    if getattr(civ, "requires_kingdom_name_pattern", None):
        conditions.append(
            strings["diplomatie_condition_pattern"].format(
                pattern=str(civ.requires_kingdom_name_pattern)
            )
        )
    if getattr(civ, "requires_tech_points", None) is not None:
        conditions.append(
            strings["diplomatie_condition_tech"].format(count=int(civ.requires_tech_points))
        )
    if getattr(civ, "requires_marriages", None) is not None:
        conditions.append(
            strings["diplomatie_condition_marriages"].format(count=int(civ.requires_marriages))
        )
    if getattr(civ, "requires_civilization_count", None) is not None:
        conditions.append(
            strings["diplomatie_condition_civ_count"].format(
                count=int(civ.requires_civilization_count)
            )
        )
    return " · ".join(conditions)


def _diplomatie_content(strings: dict[str, str], civilizations: list[Any]) -> str:
    """Build the Diplomatie rubric: civilizations + official conditions."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    maps: list[Any] = []
    try:
        wiring = _wiring()
        service = wiring.kingdoms_service
        config = getattr(service, "config", None) if service is not None else None
        maps = list(getattr(config, "maps", ()) or ())
    except Exception:
        maps = []

    lines = [strings["diplomatie_title"], ""]
    if not civilizations:
        lines.append(strings["diplomatie_empty"])
        return "\n".join(lines)
    for civ in civilizations:
        name = str(getattr(civ, "display_name", getattr(civ, "key", "?")))
        condition = _diplomatie_condition_text(civ, strings, maps)
        lines.append(f"- **{name}** — {condition}" if condition else f"- **{name}**")
    return "\n".join(lines)


def _seigneurie_content(
    strings: dict[str, str],
    kingdoms: list[Any],
    lords: list[Any],
    attack_budget: int,
    defense_budget: int,
) -> str:
    """Build the Seigneurie rubric: every lord per kingdom, remaining budgets."""

    def _budget_line(member: Any) -> str:
        attack_left = max(0, attack_budget - int(getattr(member, "attack_used", 0) or 0))
        defense_left = max(0, defense_budget - int(getattr(member, "defense_used", 0) or 0))
        return f" — ⚔️ {attack_left}/{attack_budget} · 🛡️ {defense_left}/{defense_budget}"

    lines = [strings["seigneurie_title"], ""]
    active = [lord for lord in lords if not getattr(lord, "left", False)]
    named = [k for k in kingdoms if not getattr(k, "is_gaia", False)]
    if not named:
        lines.append(strings["seigneurie_empty"])
        return "\n".join(lines)
    for kingdom in sorted(named, key=lambda k: str(k.name)):
        members = [
            lord
            for lord in active
            if getattr(lord, "kingdom_id", None) == kingdom.id and not lord.in_queue
        ]
        king = next((m for m in members if str(getattr(m.role, "value", m.role)) == "king"), None)
        head = (
            f"- 👑 {strings['seigneurie_king']} : {king.display_name}{_budget_line(king)}"
            if king is not None
            else f"- 👑 {strings['seigneurie_king']} : —"
        )
        lines.append(f"**{kingdom.name}**\n{head}")
        for member in members:
            if king is not None and member.id == king.id:
                continue
            lines.append(f"  - 🎖️ {member.display_name}{_budget_line(member)}")
        lines.append("")
    waiting = [lord for lord in active if getattr(lord, "in_queue", False)]
    if waiting:
        names = ", ".join(lord.display_name for lord in waiting)
        lines.append(f"⏳ {strings['seigneurie_pending']} : {names}")
    return "\n".join(lines)


async def _rubric_content(topic: str, strings: dict[str, str], kingdoms_service: Any) -> str:
    """Build one rubric's content from the wired services (best effort)."""
    from kingdoms.mods.kingdoms.kingdom_persistent import _wiring

    wiring = _wiring()
    service = kingdoms_service or wiring.kingdoms_service
    if service is None:
        raise RuntimeError("the Kingdoms service is not wired")
    config = getattr(service, "config", None)
    if topic == "cadastre":
        return _cadastre_content(strings, list(getattr(config, "maps", ()) or ()))
    if topic == "diplomatie":
        return _diplomatie_content(strings, list(getattr(config, "civilizations", ()) or ()))
    kingdoms = await service.kingdoms()
    lords = await service.lords()
    attacks_config = getattr(config, "attacks", None) if config is not None else None
    attack_budget = int(getattr(attacks_config, "attacks_per_week", 1) or 1)
    defense_budget = int(getattr(attacks_config, "defenses_per_week", 1) or 1)
    return _seigneurie_content(strings, kingdoms, lords, attack_budget, defense_budget)


# ----------------------------------------------------------------------
# The pinned sommaire
# ----------------------------------------------------------------------


def _scriptorium_view(strings: dict[str, str]) -> discord.ui.View:
    """Build the persistent three-button view of the pinned sommaire."""
    view = discord.ui.View(timeout=None)
    view.add_item(KingdomScriptoriumButton("cadastre", strings["topic_cadastre"]))
    view.add_item(KingdomScriptoriumButton("diplomatie", strings["topic_diplomatie"]))
    view.add_item(KingdomScriptoriumButton("seigneurie", strings["topic_seigneurie"]))
    return view


async def deploy_scriptorium_panel(
    channel: discord.TextChannel,
    locale: str,
    kingdoms_service: Any = None,
) -> bool:
    """Pin (or refresh) the Scriptorium sommaire in its salon."""
    strings = _strings(locale)
    content = f"{strings['summary_title']}\n{strings['summary_line']}\n{SCRIPTORIUM_MARKER}"
    marked: Any = None
    try:
        async for message in channel.history(limit=50):
            if SCRIPTORIUM_MARKER in (message.content or ""):
                marked = message
                break
    except Exception:
        logger.warning("SCRIPTORIUM: sommaire history scan failed", exc_info=True)
    view = _scriptorium_view(strings)
    try:
        if marked is not None:
            await marked.edit(content=content, view=view)
        else:
            await channel.send(content=content, view=view)
        return True
    except Exception:
        logger.warning("SCRIPTORIUM: sommaire send failed", exc_info=True)
        return False


def find_scriptorium_channel(guild: discord.Guild | None) -> discord.TextChannel | None:
    """Return the guild's 📜 Scriptorium salon (None when absent)."""
    if guild is None:
        return None
    wanted = _slug(SCRIPTORIUM_CHANNEL)
    return next(
        (c for c in getattr(guild, "text_channels", []) if _slug(getattr(c, "name", "")) == wanted),
        None,
    )


# ----------------------------------------------------------------------
# The persistent rubric button
# ----------------------------------------------------------------------


class KingdomScriptoriumButton(
    discord.ui.DynamicItem[discord.ui.Button[Any]],
    template=r"kingdoms:scriptorium:(?P<topic>cadastre|diplomatie|seigneurie)",
):
    """The restart-proof rubric button of the pinned Scriptorium sommaire."""

    def __init__(self, topic: str, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                custom_id=f"kingdoms:scriptorium:{topic}",
                style=discord.ButtonStyle.secondary,
            )
        )
        self.topic = topic

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Item[Any],
        match: re.Match[str],
        /,
    ) -> KingdomScriptoriumButton:
        """Rebuild the button from the wire — THIS module's strings only."""
        strings = _strings(_locale(interaction))
        return cls(str(match.group("topic")), strings[f"topic_{match.group('topic')}"])

    async def callback(self, interaction: discord.Interaction) -> None:
        """Answer with the topic's EPHEMERAL view (public rubric, market TTL)."""
        from kingdoms.mods.kingdoms.kingdom_persistent import _market_ttl

        strings = _strings(_locale(interaction))
        try:
            content = await _rubric_content(self.topic, strings, None)
        except Exception:
            logger.warning("SCRIPTORIUM: rubric build failed", exc_info=True)
            await interaction.response.send_message(strings["unknown"], ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
            message = cast(Any, await interaction.followup.send(content, ephemeral=True))
            _market_ttl(interaction, message)
        except Exception:
            logger.warning("SCRIPTORIUM: ephemeral send failed", exc_info=True)
