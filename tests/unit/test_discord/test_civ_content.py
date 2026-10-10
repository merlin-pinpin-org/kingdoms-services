"""Unit tests for the civ help-text parser (HTML sections to bullets)."""

from __future__ import annotations

from kingdoms.discord.civ_content import civ_section_lines, parse_civ_help

FRANKS_HELP = (
    "Cavalry civilization<br>\n<br>\n"
    "\u2022 Foragers work +15% faster<br>\n"
    "\u2022 Mill technologies free<br>\n"
    "\u2022 Mounted Units +20% HP starting in Feudal Age<br>\n"
    "\u2022 Castles cost -15/25% in Castle/Imperial Age<br>\n"
    "<br>\n<b>Unique Unit:</b> <br>\n"
    "Throwing Axeman (Infantry)<br>\n<br>\n"
    "<b>Unique Techs:</b> <br>\n"
    "\u2022 Ordonnance Companies (Mounted Crossbowmen cost -40% gold)<br>\n"
    "\u2022 Chivalry (Stables work +40% faster)<br>\n"
    "<br>\n<b>Team Bonus:</b> <br>\n"
    "Knight-line +2 line of sight"
)

FRENCH_HELP = (
    "Civilisation de cavalerie<br><br>"
    "\u2022 Les cueilleurs travaillent 15\u00a0% plus vite<br>"
    "<b>Unit\u00e9 unique\u00a0: </b> <br>"
    "Lanceur de hache (infanterie)<br>"
    "<b>Bonus d'\u00e9quipe\u00a0: </b> <br>"
    "Chevaliers +2 de ligne de mire"
)


def test_parse_english_help_sections() -> None:
    """The Franks help parses into typed sections, HTML gone."""
    content = parse_civ_help(FRANKS_HELP)
    assert content.civ_type == "Cavalry civilization"
    assert "Foragers work +15% faster" in content.bonuses
    assert content.unique_unit == "Throwing Axeman (Infantry)"
    assert len(content.unique_techs) == 2
    assert content.team_bonus == "Knight-line +2 line of sight"
    assert "<b>" not in content.raw and "<br>" not in content.raw


def test_parse_french_help_sections() -> None:
    """The French labels resolve to the same sections (nbsp handled)."""
    content = parse_civ_help(FRENCH_HELP)
    assert content.civ_type == "Civilisation de cavalerie"
    assert content.unique_unit == "Lanceur de hache (infanterie)"
    assert content.team_bonus == "Chevaliers +2 de ligne de mire"


def test_section_lines_cover_every_part() -> None:
    """The rendered sections carry every parsed part, labeled."""
    sections = civ_section_lines(parse_civ_help(FRANKS_HELP))
    labels = [label for label, _ in sections]
    assert labels == [
        "Type de civilisation",
        "Bonus de civilisation",
        "Unité unique",
        "Technologies uniques",
        "Bonus d'équipe",
    ]
    body = "\n".join("\n".join(items) for _, items in sections)
    assert "Throwing Axeman" in body and "Chivalry" in body
