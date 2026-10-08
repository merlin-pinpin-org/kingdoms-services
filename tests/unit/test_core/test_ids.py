"""Visible id builders and footers — unit tests (kingdoms.core.ids)."""

from __future__ import annotations

from kingdoms.core.ids import footer, ladder_id, mod_scope, season_id, territory_id


def test_ladder_id_embeds_game_and_guild() -> None:
    assert ladder_id("aoe2", "123") == "ladder:123:aoe2"


def test_season_id_is_ladder_plus_incremental_index() -> None:
    assert season_id(ladder_id("aoe2", "123"), 1) == "ladder:123:aoe2:1"


def test_mod_scope_embeds_mod_game_and_guild() -> None:
    assert mod_scope("kingdoms", "aoe2", "123") == "kingdoms:aoe2:123"


def test_territory_id_embeds_season_and_map_key() -> None:
    assert territory_id("kingdoms:123:aoe2:1", "arabia") == "territory:kingdoms:123:aoe2:1:arabia"


def test_footer_renders_small_text_ids() -> None:
    assert footer("a-1", "b-2") == "-# `a-1` · `b-2`"


def test_footer_drops_empty_ids_and_is_empty_without_any() -> None:
    assert footer("a-1", "") == "-# `a-1`"
    assert footer() == ""
