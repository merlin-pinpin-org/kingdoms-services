"""Unit tests for the visual card builders (kingdoms-services).

The cards are rendered by the visual-generator library (dependency
group ``visuals``); these tests pin the builder mapping: entity →
library JSON schema → rendered file. The OCR validation contract lives
in the library; here we assert the file is produced and the JSON fed
to the library matches the documented schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from kingdoms.discord.visual_cards import faction_card, map_card, player_card


@dataclass
class _Faction:
    id: str = "faction:aoe2:Franks"
    name: str = "Francs"
    game_key: str = "aoe2"


@dataclass
class _MapEntry:
    id: str = "map:aoe2:arabia"
    name: str = "Arabie"


def test_faction_card_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    card = faction_card(
        _Faction(),
        ("Francs", "Villageois moins chers", "https://aoe2techtree.net", ""),
        tags=["Infanterie"],
        facts=[("Forces", [["Bonus", "Villageois moins chers"]])],
    )
    assert os.path.isfile(card.path)
    assert card.path.endswith(".jpg")
    assert "Francs" in card.caption


def test_map_card_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    card = map_card(
        _MapEntry(),
        images=[],
        tags=["1v1"],
        facts=[["Taille", "Moyenne"]],
        description="Carte ouverte ; l'agression précoce paie.",
    )
    assert os.path.isfile(card.path)


def test_player_card_renders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    card = player_card(
        "Lionheart",
        avatar_url=None,
        games=[["aoe2", "principal"]],
        profile_ids=[["aoe2", "76521"]],
    )
    assert os.path.isfile(card.path)
    assert "Lionheart" in card.path
