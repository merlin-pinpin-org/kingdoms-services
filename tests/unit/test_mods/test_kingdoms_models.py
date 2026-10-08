"""Kingdoms mod models — Mongo round-trip unit tests.

The models are strict (``ConfigDict(strict=True)``) for in-code
construction, but pymongo stores enum values as plain strings: the
``from_mongo`` read must accept them (the 2026-10-07 drasah regression
made every kingdom read raise ``Input should be an instance of
KingdomType``).
"""

from __future__ import annotations

from kingdoms.mods.kingdoms.models import (
    GAIA_KINGDOM_KEY,
    KingdomModel,
    KingdomType,
    LordModel,
    LordRole,
)


def test_kingdom_model_round_trips_through_mongo_strings() -> None:
    """to_mongo → from_mongo survives the plain-string enum storage."""
    kingdom = KingdomModel(
        _id=GAIA_KINGDOM_KEY,
        season_id="s-20261008",
        type=KingdomType.GAIA,
        name="Gaïa",
    )
    doc = kingdom.to_mongo()
    # pymongo writes the StrEnum as a plain string — simulate that here.
    doc["type"] = str(doc["type"])
    revived = KingdomModel.from_mongo(doc)
    assert revived.type is KingdomType.GAIA
    assert revived.is_gaia


def test_lord_model_round_trips_through_mongo_strings() -> None:
    """to_mongo → from_mongo survives the plain-string role storage."""
    lord = LordModel(
        _id="player-1",
        season_id="s-20261008",
        kingdom_id="k-1",
        role=LordRole.KING,
        display_name="Drasah",
    )
    doc = lord.to_mongo()
    doc["role"] = str(doc["role"])
    revived = LordModel.from_mongo(doc)
    assert revived.role is LordRole.KING
