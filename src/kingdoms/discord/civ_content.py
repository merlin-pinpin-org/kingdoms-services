"""Structured civ-content parsing: HTML help text to Discord sections.

The aoe2techtree help strings carry light HTML separators (``<br>``,
``<b>...</b>``) and French non-breaking spaces. This module turns one
help string into labeled sections (civilization type, civilization
bonuses, unique unit, unique techs, team bonus) that the forums render
as clean Discord components instead of a raw HTML blob.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_B_RE = re.compile(r"<b>(.*?)</b>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_NBSP_RE = re.compile("\u00a0")

_SECTION_LABELS = {
    "unique unit": "unique_unit",
    "unité unique": "unique_unit",
    "unique tech": "unique_techs",
    "unique techs": "unique_techs",
    "tech uniques": "unique_techs",
    "techs uniques": "unique_techs",
    "technologies uniques": "unique_techs",
    "team bonus": "team_bonus",
    "bonus d'équipe": "team_bonus",
    "bonus d' équipe": "team_bonus",
}


@dataclass
class CivContent:
    """One civ's parsed help content, ready for Discord components."""

    civ_type: str = ""
    bonuses: list[str] = field(default_factory=list)
    unique_unit: str = ""
    unique_techs: list[str] = field(default_factory=list)
    team_bonus: str = ""
    raw: str = ""


def _strip_html(value: str) -> str:
    """Drop tags and normalize spaces (nbsp, repeated whitespace)."""
    text = _TAG_RE.sub("", value)
    text = text.replace("&nbsp;", " ")
    text = _NBSP_RE.sub(" ", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _split_bullets(value: str) -> list[str]:
    """Split a section body into bullet items (• or bullet lines)."""
    items = [part.strip() for part in re.split(r"[•\u2022]", value) if part.strip()]
    return [re.sub(r"\s*\n\s*", " ", item) for item in items]


def parse_civ_help(help_text: str) -> CivContent:
    """Parse one civ's help string into labeled sections.

    The layout is stable across civs and locales: the type line first,
    then the civilization bonuses, then bold-labeled sections (unique
    unit, unique techs, team bonus). Anything unmatched lands in the
    bonuses; the labels are matched in both English and French.
    """
    raw = help_text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    content = CivContent(raw=_strip_html(raw))
    lines = [line.strip() for line in raw.split("\n")]

    sections: dict[str, list[str]] = {}
    current = "bonuses"
    for line in lines:
        text = _strip_html(line)
        if not text:
            continue
        bold = _B_RE.search(line)
        if bold:
            label = _strip_html(bold.group(1)).rstrip(":").strip().lower()
            key = _SECTION_LABELS.get(label)
            if key is not None:
                current = key
                tail = _strip_html(_B_RE.sub("", line)).rstrip(":").strip()
                if tail:
                    sections.setdefault(current, []).append(tail)
                continue
        sections.setdefault(current, []).append(text)

    if sections.get("bonuses"):
        first = sections["bonuses"][0]
        if not first.startswith("•") and "•" not in first[:2] and current != "bonuses":
            content.civ_type = first
            sections["bonuses"] = sections["bonuses"][1:]
        elif "\n" not in first and len(first) < 60 and "civilization" in first.lower():
            content.civ_type = first
            sections["bonuses"] = sections["bonuses"][1:]

    content.bonuses = _split_bullets(" ".join(sections.get("bonuses", [])))
    unit_lines = sections.get("unique_unit", [])
    content.unique_unit = re.sub(r"\s*\n\s*", " ", " ".join(unit_lines)).strip()
    techs = _split_bullets(" ".join(sections.get("unique_techs", [])))
    content.unique_techs = [t for t in techs if t]
    team = re.sub(r"\s*\n\s*", " ", " ".join(sections.get("team_bonus", []))).strip()
    content.team_bonus = team
    return content


def civ_section_lines(content: CivContent) -> list[tuple[str, list[str]]]:
    """Build the labeled section bodies for a civ post."""
    sections: list[tuple[str, list[str]]] = []
    if content.civ_type:
        sections.append(("Type de civilisation", [content.civ_type]))
    if content.bonuses:
        sections.append(("Bonus de civilisation", content.bonuses))
    if content.unique_unit:
        sections.append(("Unité unique", [content.unique_unit]))
    if content.unique_techs:
        sections.append(("Technologies uniques", content.unique_techs))
    if content.team_bonus:
        sections.append(("Bonus d'équipe", [content.team_bonus]))
    return sections
