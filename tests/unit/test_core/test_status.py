"""Unit tests for the status capability (StatusService + /status embed)."""

from __future__ import annotations

from kingdoms.core.services.mod_definition import (
    ChannelCategoryDef,
    ModDefinition,
    RoleDef,
)
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.status import StatusService, parse_bot_admins


def make_registry() -> ModRegistry:
    return ModRegistry(
        {
            "example": ModDefinition(
                name="example",
                channel_categories=(ChannelCategoryDef(key="announce", display_name="Annonces"),),
                roles=(RoleDef(key="member", display_name="Example Member"),),
            ),
            "off": ModDefinition(name="off", enabled=False),
        }
    )


def test_parse_bot_admins_comma_separated_ids() -> None:
    admins = parse_bot_admins("111, 222 ,333")
    assert admins.user_ids == ("111", "222", "333")


def test_parse_bot_admins_ignores_garbage_and_empty() -> None:
    assert parse_bot_admins(None).user_ids == ()
    assert parse_bot_admins("").user_ids == ()
    assert parse_bot_admins("abc,  ,, 123x").user_ids == ()


def test_status_service_report_shape() -> None:
    service = StatusService(registry=make_registry(), bot_admins=parse_bot_admins("42"), games=("aoe2",))
    report = service.report()
    assert report["version"]
    assert report["uptime_seconds"] == 0.0
    assert report["bot_admins"] == ("42",)
    assert report["deploy_url"] == ""
    assert report["games"] == ("aoe2",)
    assert report["enabled_mods"] == {"example": {"channels": ("announce",), "roles": ("member",)}}


def test_status_service_exposes_deploy_url() -> None:
    service = StatusService(
        registry=make_registry(),
        bot_admins=parse_bot_admins("42"),
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/12",
    )
    assert service.deploy_url == "https://github.com/merlin-pinpin-org/kingdoms-services/pull/12"
    assert service.report()["deploy_url"] == "https://github.com/merlin-pinpin-org/kingdoms-services/pull/12"


def test_status_service_strips_deploy_url_whitespace() -> None:
    service = StatusService(registry=make_registry(), bot_admins=parse_bot_admins(""), deploy_url="  ")
    assert service.deploy_url == ""


def test_status_service_disabled_mods_excluded() -> None:
    service = StatusService(registry=make_registry(), bot_admins=parse_bot_admins("42"))
    assert "off" not in service.enabled_mods()


def test_status_service_uptime_uses_injected_clock() -> None:
    ticks = [0.0]

    def clock() -> float:
        return ticks[0]

    service = StatusService(registry=make_registry(), bot_admins=parse_bot_admins(""), clock=clock)
    ticks[0] = 65.0
    assert service.uptime_seconds() == 65.0


def test_status_report_is_json_serializable() -> None:
    import json

    service = StatusService(registry=make_registry(), bot_admins=parse_bot_admins("42"))
    json.dumps(service.report())
