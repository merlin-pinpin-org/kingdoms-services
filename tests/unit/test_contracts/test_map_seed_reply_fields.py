"""The MapSeedReply wire carries every field the servicer builds.

The 2026-11-06 incident: a proto edit silently failed (no assert on
the replace), the stubs regenerated WITHOUT the new fields while the
servicer code referenced them — the provider crashed at runtime
('Protocol message MapSeedReply has no walls_label field') and the
CI stayed green: the wire-compat check compares stubs against the
proto (both stale — consistent), and mypy excludes rpc_generated.

This canary constructs the reply with EVERY field the servicer
builds: a stub/proto/code drift fails HERE, at commit time.
"""

from __future__ import annotations


def test_map_seed_reply_carries_every_servicer_field() -> None:
    """Build the reply exactly as the servicer does — every kwarg must exist."""
    from kingdoms.rpc_generated.kingdoms.v1 import content_pb2

    reply = content_pb2.MapSeedReply(
        found=True,
        name="Canberra",
        description="A palisade arena protects players.",
        resource_url="https://liquipedia.net/ageofempires/Canberra",
        image_url="https://liquipedia.net/Special:FilePath/Canberra.png",
        icon_url="https://liquipedia.net/Special:FilePath/Icon.png",
        terrain="",
        size="",
        map_type="Land",
        licence_ok=True,
        licence_note="",
        walls=False,
        nomad=False,
        tc=False,
        walls_label="No",
        nomad_label="No",
        tc_label="1",
        player_capacity="Up to 8",
        creator="Forgotten Empires",
        game_versions=["The African Kingdoms"],
        tags=["land"],
    )
    assert reply.walls_label == "No"
    assert reply.tc_label == "1"
    assert reply.player_capacity == "Up to 8"
    assert reply.creator == "Forgotten Empires"


def test_the_servicer_source_only_uses_fields_the_wire_declares() -> None:
    """Every MapSeedReply kwarg in the servicer must exist on the stub.

    The AST guard the incident demanded: a servicer building a field
    the wire does not declare fails HERE — never in production.
    """
    import inspect
    import re

    from kingdoms.core.rpc import content as content_servicer
    from kingdoms.rpc_generated.kingdoms.v1 import content_pb2

    source = inspect.getsource(content_servicer)
    built = set(re.findall(r"MapSeedReply\(([^)]*)", source, re.DOTALL))
    kwargs: set[str] = set()
    for block in built:
        kwargs |= set(re.findall(r"(\w+)=", block))
    declared = set(content_pb2.MapSeedReply.DESCRIPTOR.fields_by_name)
    drift = kwargs - declared - {"found"}
    assert not drift, f"the servicer builds fields the wire does not declare: {sorted(drift)}"
