"""The Jinja card templating: declared balises, obj + helpers, notices.

The rules (owner direction, 2026-11-13): an unfilled balise renders
the missing-data notice naming EVERY missing key; a balise set to
None is skipped silently; the values compose the link, mention and
date helpers with the model object in scope.
"""

from __future__ import annotations

from types import SimpleNamespace

from botforge.discord.ui.card_templating import (
    CIV_CARD_TEMPLATE,
    POOL_CARD_TEMPLATE,
    DateHelpers,
    LinkHelpers,
    MentionHelpers,
    render_balises,
    templated_entity_card,
)


def test_a_filled_balise_rides_its_zone() -> None:
    zones = render_balises(
        POOL_CARD_TEMPLATE,
        None,
        {"state": "**État** : ✅ **Publié** · 2 map(s)", "summary": "Une rotation rapide."},
        locale="fr",
    )
    assert zones["props"] == ["**État** : ✅ **Publié** · 2 map(s)"]
    assert zones["body"] == ["Une rotation rapide."]


def test_a_missing_balise_names_every_missing_key_in_the_notice() -> None:
    zones = render_balises(POOL_CARD_TEMPLATE, None, {"state": "ok"}, locale="fr")
    joined = "\n".join(zones["body"])
    assert "Il manque de la data" in joined
    assert "`summary`" in joined


def test_a_none_balise_is_skipped_silently() -> None:
    zones = render_balises(
        POOL_CARD_TEMPLATE,
        None,
        {"state": "ok", "summary": None},
        locale="fr",
    )
    assert "body" not in zones
    assert "Il manque de la data" not in str(zones)


def test_the_values_compose_the_helpers_with_obj_in_scope() -> None:
    obj = SimpleNamespace(
        guild_id="42", mod_post_id="111", owner_id="7", deadline=1700000000, channel_id="99", role_id="5"
    )
    values = {
        "state": "Mod : {{ link.post(obj.guild_id, obj.mod_post_id) }}",
        "summary": "Par {{ mention.user(obj.owner_id) }} dans {{ mention.channel(obj.channel_id) }} "
        "rôle {{ mention.role(obj.role_id) }} — fin {{ date.at(obj.deadline) }}",
    }
    zones = render_balises(POOL_CARD_TEMPLATE, obj, values, locale="fr")
    assert "https://discord.com/channels/42/111/111" in zones["props"][0]
    assert "<@7>" in zones["body"][0]
    assert "<#99>" in zones["body"][0]
    assert "<@&5>" in zones["body"][0]
    assert "<t:1700000000:R>" in zones["body"][0]


def test_the_date_formatter_defaults_to_relative_and_takes_a_style() -> None:
    assert DateHelpers.at(1700000000) == "<t:1700000000:R>"
    assert DateHelpers.at(1700000000, style="F") == "<t:1700000000:F>"


def test_the_mention_and_link_helpers_cover_the_discord_shapes() -> None:
    assert MentionHelpers.user(1) == "<@1>"
    assert MentionHelpers.channel(2) == "<#2>"
    assert MentionHelpers.role(3) == "<@&3>"
    assert LinkHelpers.message(1, 2, 3) == "https://discord.com/channels/1/2/3"
    assert LinkHelpers.post(1, 3) == "https://discord.com/channels/1/3/3"
    assert LinkHelpers.channel(2) == "https://discord.com/channels/2"


def test_the_templated_card_lands_the_zones_on_the_entity_card() -> None:
    view = templated_entity_card(
        CIV_CARD_TEMPLATE,
        None,
        {"type": "Infanterie", "summary": "Fort en début de partie."},
        title="Britons",
        entity_id="civ:aoe2:britons",
        locale="fr",
        view_id="panel:civ:aoe2:britons",
    )

    def _texts(node: object) -> list[str]:
        out: list[str] = []
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        for child in getattr(node, "children", ()) or ():
            out.extend(_texts(child))
        return out

    texts = [t for child in view.children for t in _texts(child)]
    assert texts[0].startswith("## Britons\n`civ:aoe2:britons`\nInfanterie")
    assert "Fort en début de partie." in "\n".join(texts)
    assert texts[-1] == "-# `panel:civ:aoe2:britons`"
