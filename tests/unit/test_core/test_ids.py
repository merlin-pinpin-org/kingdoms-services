"""Visible id builders and footers — unit tests (kingdoms.core.ids)."""

from __future__ import annotations

from kingdoms.core.ids import (
    footer,
    ladder_id,
    map_entry_id,
    mod_scope,
    pool_entry_id,
    season_id,
    territory_id,
    view_key,
)


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


def test_map_entry_id_embeds_game_and_scope() -> None:
    assert map_entry_id("aoe2", "Land Nomad", None) == "map:aoe2:public:land_nomad"
    assert map_entry_id("aoe2", "Arabia", "155") == "map:aoe2:155:arabia"


def test_pool_entry_id_embeds_game_and_scope() -> None:
    assert pool_entry_id("aoe2", "Season 1", None) == "pool:aoe2:public:season_1"
    assert pool_entry_id("aoe2", "S1", "155") == "pool:aoe2:155:s1"


def test_view_key_is_scope_then_slug() -> None:
    assert view_key("aoe2", "live")[0] == "view:aoe2:live"
    assert view_key("bot", "admin")[0] == "view:bot:admin"
    assert view_key("ladder", "news")[0] == "view:ladder:news"
