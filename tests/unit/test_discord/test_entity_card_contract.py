"""The pseudo-embed card contract (issue #321).

Every card family — map, pool, civ — follows THE SAME layout: the
resource id in backticks as the subtitle's first line, and the id
footer ALWAYS the last text block. The test fails on any family that
drifts (an id scattered by state, a footer above the buttons).
"""

from __future__ import annotations

from typing import Any

import discord

from botforge.discord.ui.entity_cards import civ_card, map_card, pool_card


def _texts(view: Any) -> list[str]:
    """Every TextDisplay's content, layout order (containers walked)."""
    out: list[str] = []

    def _visit(node: Any) -> None:
        content = getattr(node, "content", None)
        if isinstance(content, str):
            out.append(content)
        for child in getattr(node, "children", ()) or ():
            _visit(child)

    for child in view.children:
        _visit(child)
    return out


def _button() -> Any:
    return discord.ui.Button(label="Voir", custom_id="map:open:x:y")


def test_map_card_carries_the_id_in_subtitle_and_footer() -> None:
    view = map_card(
        map_id="map:aoe2:public:arabia",
        name="Arabia",
        props=("open",),
        description="The classic open map.",
        image_url="",
        icon_url="",
        source_url="https://liquipedia.net/aoe2/Arabia",
        pool_links=("Pool 1",),
        buttons=(_button,),
    )
    texts = _texts(view)
    assert texts[0].startswith("## Arabia\n`map:aoe2:public:arabia`")
    assert texts[-1] == "-# `panel:map:aoe2:public:arabia`"


def test_pool_card_carries_the_id_in_subtitle_and_footer_whatever_the_state() -> None:
    for state_line in ("**État** : ✍🏻 **Édition**", "**État** : ✅ **Publié**", "**État** : ⛔ **Archivé**"):
        view = pool_card(
            pool_id="map_pool:aoe2:p1",
            name="Rotation 1",
            state_line=state_line,
            description="Desc",
            member_sections=(("Arabia", "https://l", "https://x/a.png"),),
            buttons=(_button,),
        )
        texts = _texts(view)
        assert texts[0].startswith("## Rotation 1\n`map_pool:aoe2:p1`"), state_line
        assert texts[-1] == "-# `panel:map_pool:aoe2:p1`", state_line


def test_civ_card_carries_the_id_in_subtitle_and_footer() -> None:
    view = civ_card(
        civ_id="civ:aoe2:britons",
        name="Britons",
        civ_type="Infanterie",
        sections=(("Bonus", ("Shepherd +25%",)),),
        image_url="https://x/c.png",
        source_url="https://aoe2techtree.net/britons.html",
        source_label="aoe2techtree",
    )
    texts = _texts(view)
    assert texts[0].startswith("## Britons\n`civ:aoe2:britons`")
    assert texts[-1] == "-# `panel:civ:aoe2:britons`"


def test_the_chrome_labels_localize_per_locale() -> None:
    view_fr = civ_card(
        civ_id="civ:aoe2:britons",
        name="Britons",
        civ_type="",
        sections=(),
        image_url="",
        source_url="https://aoe2techtree.net/britons.html",
        source_label="aoe2techtree",
        locale="fr",
    )
    view_en = civ_card(
        civ_id="civ:aoe2:britons",
        name="Britons",
        civ_type="",
        sections=(),
        image_url="",
        source_url="https://aoe2techtree.net/britons.html",
        source_label="aoe2techtree",
        locale="en",
    )
    assert "Source :" in " ".join(_texts(view_fr))
    assert "Source: " in " ".join(_texts(view_en))


def test_the_builder_chain_builds_the_same_card() -> None:
    """The fluent builder is the SDK's 90% path for a new entity."""
    from botforge.discord.ui.entity_cards import EntityCardBuilder

    view = (
        EntityCardBuilder("kingdoms:territory:anjou", "Anjou", locale="fr")
        .subtitle("Comté · féodal")
        .props("Ressource : blé")
        .description("Un territoire de plaine.")
        .thumbnail("https://x/art.png")
        .source("https://wiki/anjou", label="Liquipedia")
        .buttons(lambda: _button())
        .build()
    )
    texts = _texts(view)
    assert texts[0].startswith("## Anjou\n`kingdoms:territory:anjou`\nComté · féodal")
    assert texts[-1] == "-# `panel:kingdoms:territory:anjou`"


def test_the_extra_seam_composes_a_group_without_forking_the_template() -> None:
    """A shape no spec field expresses rides the extra seam."""
    import discord

    from botforge.discord.ui.entity_cards import EntityCardBuilder

    def _banner(spec: object) -> list[discord.ui.Item[discord.ui.LayoutView]]:
        return [discord.ui.TextDisplay("**Nouveau**")]

    view = (
        EntityCardBuilder("kingdoms:territory:anjou", "Anjou")
        .description("Desc")
        .extra(_banner)  # noqa: S610 — the card builder's seam, not Django
        .build()
    )
    joined = "\n".join(_texts(view))
    assert "**Nouveau**" in joined
    # the extra group rides INSIDE the template: behind its separators,
    # before the credits, under the same budget
    assert "**Nouveau**" in joined.split("Source")[0]
