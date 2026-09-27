"""Unit tests for the startup announcement (kingdoms-services#52, #109).

The announcement is the "start" lifecycle event, delivered by the core
LogService to each guild's 🤖-bot-logs channel. These tests pin the
Components V2 layout contract (generic button labels — the identity
rides in the text lines above each row, the full image digest on its
own line, the uptime as a Discord relative timestamp, no footer, no
rollback button — Services/Infra/Bot sections) and the wiring
contracts: with a LogService the event flows to every guild; without
one (local runs, unit tests) the announcement degrades to a silent
skip. KINGDOMS_ANNOUNCE_ENABLED=0 silences it entirely (CI/CD bot).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import discord
import pytest

from kingdoms.core.services.logs import LifecycleEvent, LogService
from kingdoms.core.services.mod_registry import ModRegistry
from kingdoms.core.services.status import BotAdmins, StatusService
from kingdoms.discord.announce import (
    AnnounceConfig,
    announce_startup,
    build_announcement_layout,
)

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _status_service(**kwargs: str) -> StatusService:
    return StatusService(registry=ModRegistry({}), bot_admins=BotAdmins(), **kwargs)


class _FakeLogService:
    """LogService stand-in capturing the delivered events per guild."""

    def __init__(self) -> None:
        self.events: dict[str, list[LifecycleEvent]] = {}
        self.pinned = False

    async def log_event(self, guild_id: str, event: LifecycleEvent, *, pin: bool = False) -> None:
        self.events.setdefault(guild_id, []).append(event)
        self.pinned = pin


class _Bot:
    """Client stand-in exposing the guilds and the gateway latency."""

    def __init__(self, guild_ids: list[str], latency: float | None = None) -> None:
        self.guilds = [type("G", (), {"id": int(gid)})() for gid in guild_ids]
        self.latency = latency


TYPE_TEXT_DISPLAY = 10
TYPE_SEPARATOR = 14
TYPE_CONTAINER = 17
TYPE_ACTION_ROW = 1
TYPE_BUTTON = 2
TYPE_SECTION = 9


def _walk(components: Any) -> list[dict[str, Any]]:
    """Depth-first walk of a wire component tree (children + accessories)."""
    out: list[dict[str, Any]] = []
    for component in components:
        out.append(component)
        out.extend(_walk(component.get("components", [])))
        accessory = component.get("accessory")
        if accessory:
            out.append(accessory)
            out.extend(_walk(accessory.get("components", [])))
    return out


def _iter_texts(components: Any) -> list[str]:
    """Flatten every TextDisplay content of a wire V2 component tree."""
    return [c["content"] for c in _walk(components) if c.get("type") == TYPE_TEXT_DISPLAY]


def _iter_buttons(components: Any) -> list[dict[str, Any]]:
    """Flatten every button of a wire V2 component tree."""
    return [c for c in _walk(components) if c.get("type") == TYPE_BUTTON]


def test_layout_is_components_v2() -> None:
    status = _status_service(
        deploy_label="pr-42-x",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/42#issuecomment-1",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    assert isinstance(layout, discord.ui.LayoutView)
    assert layout.to_components(), "the layout must serialize to V2 components"
    texts = _iter_texts(layout.to_components())
    joined = "\n".join(texts)
    assert "Kingdoms — Deployment" in joined
    assert "`test`" in joined
    assert not any("[" in text and "](" in text for text in texts), "no markdown links in V2 text blocks"
    assert "kingdoms-deploy" not in joined, "no machine footer"
    assert not any("-# /status" in text for text in texts), "no footer line"


def test_layout_buttons_use_generic_labels() -> None:
    """Buttons carry generic labels (Branch, Commit, ...) \u2014 the identity
    (branch, sha7, tag, run number) rides in the text lines above."""
    status = _status_service(
        deploy_branch="vibe/feature-1",
        deploy_kind="pr",
        deploy_ref="42",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/pull/42",
        deploy_tree_url="https://github.com/merlin-pinpin-org/kingdoms-services/tree/abc1234deadbeef",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-20260925-abc1234",
        deploy_ci_run_id="987654",
        deploy_ci_run_number="321",
        deploy_run_url="https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1",
        deploy_run_number="45",
        deploy_infra_label="deploy/test@c232b34deadbeef",
        deploy_infra_url="https://github.com/merlin-pinpin-org/kingdoms-infra/tree/c232b34deadbeef",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = "\n".join(_iter_texts(layout.to_components()))
    labels = [b["label"] for b in _iter_buttons(layout.to_components())]
    assert "\U0001f33f Branch" in labels, "branch button carries a generic label"
    assert "\U0001f527 Commit" in labels, "commit button carries a generic label"
    assert any(label.endswith("Files") for label in labels), "files button present"
    assert "\U0001f9ea Job" in labels, "CI job button present, labeled Job"
    assert "\U0001f4e6 Image" in labels, "image button present"
    assert any(label.startswith("\U0001f680 Job") for label in labels), "deploy button present, labeled Job"
    assert not any("vibe/feature-1" in label or "abc1234" in label or "c232b34" in label for label in labels), (
        "the identity rides in the text lines, not the button labels"
    )
    assert not any("Rollback" in label for label in labels), "no rollback button (no workflow yet)"
    assert "vibe/feature-1" in texts and "abc1234" in texts and "#45" in texts, (
        "the identity is displayed in the text lines"
    )
    ci_button = next(b for b in _iter_buttons(layout.to_components()) if b["label"] == "\U0001f9ea Job")
    assert ci_button["url"] == "https://github.com/merlin-pinpin-org/kingdoms-services/actions/runs/987654", (
        "the CI button links the services CI job, not the infra deploy run"
    )
    assert any("Job CI" in text and "#321" in text for text in _iter_texts(layout.to_components())), (
        "the CI job id rides in the Job CI line"
    )


def test_image_line_renders_the_full_tag_and_digest() -> None:
    """The image line renders the docker tag with its build timestamp;
    the full digest sits on its own line below \u2014 never truncated,
    never inline."""
    digest64 = "0123456789abcdef" * 4
    status = _status_service(
        deploy_image=f"ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-20260925222854-abc1234@sha256:{digest64}",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = _iter_texts(layout.to_components())
    image_block = next(t for t in texts if "\U0001f4e6" in t and "pr-42-20260925222854-abc1234" in t)
    lines = image_block.splitlines()
    assert lines[0].startswith("\U0001f4e6 Image"), "the tag headlines the image block"
    assert f"`sha256:{digest64}`" in lines, "the full digest sits on its own line"
    assert len(lines[-1]) == len(f"`sha256:{digest64}`"), "the digest is complete (64 hex chars)"
    assert "(sha256:" not in "\n".join(texts), "the digest never decorates the tag inline"


def test_timestamps_sit_under_commit_and_build_for_both_repos() -> None:
    """Services: commit date under the commit line, build date under the
    image line. Infra: commit date under the state commit line, run
    date under the deployment line."""
    status = _status_service(
        deploy_branch="vibe/feature-1",
        deploy_tree_url="https://github.com/merlin-pinpin-org/kingdoms-services/tree/abc1234deadbeef",
        deploy_commit_ts="1790000000",
        deploy_ts="1790100000",
        deploy_image="ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x",
        deploy_infra_label="deploy/test@abc1234deadbeef",
        deploy_infra_commit_ts="1790050000",
        deploy_run_url="https://github.com/merlin-pinpin-org/kingdoms-infra/actions/runs/1",
        deploy_run_number="45",
        deploy_run_ts="1790150000",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = _iter_texts(layout.to_components())
    services = next(t for t in texts if "Branch" in t and "abc1234" in t and "2026" not in t)
    assert "🔧 Commit `abc1234` <t:1790000000:R>" in services.splitlines()
    image_line = next(t for t in texts if "📦" in t and "<t:1790100000:R>" in t)
    assert image_line
    infra = next(t for t in texts if "deploy/test" in t)
    assert "🔧 Commit `abc1234` <t:1790050000:R>" in infra.splitlines()
    deploy_line = next(t for t in texts if "🚀" in t and "<t:1790150000:R>" in t)
    assert deploy_line.startswith("🚀 Job deployment `#45`"), "the deploy job id headlines the line"
    kinds = [c["type"] for c in layout.to_components()[0]["components"]]
    assert kinds.count(TYPE_SEPARATOR) == 3, "Bot/Services, Services/Infra, deploy job (no CI data in this fixture)"


def test_layout_bot_section_reports_uptime_admins_games_mods_latency() -> None:
    """The Bot section mirrors /status: uptime, admins as mentions,
    games, enabled mods, gateway latency."""

    def advancing_clock() -> float:
        advancing_clock.now += 3661.0
        return advancing_clock.now

    advancing_clock.now = 0.0
    status = StatusService(
        registry=ModRegistry({}),
        bot_admins=BotAdmins(user_ids=("111", "222")),
        games=("werewolf", "alliance"),
        clock=advancing_clock,
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    before = time.time()
    layout = build_announcement_layout(status, config, env="test", latency_ms=42)
    texts = "\n".join(_iter_texts(layout.to_components()))
    assert "**Bot**" in texts
    uptime_line = next(line for line in texts.splitlines() if "Uptime:" in line)
    assert "<t:" in uptime_line and ":R>" in uptime_line, "the uptime renders as a Discord relative date"
    boot_unix = int(uptime_line.split("<t:", 1)[1].split(":", 1)[0])
    assert before - 3661.0 - 1 <= boot_unix <= time.time(), "the boot timestamp matches the uptime"
    assert "<@111>" in texts and "<@222>" in texts
    assert "Games: werewolf, alliance" in texts
    assert "Latency: 42 ms" in texts


def test_layout_bot_section_without_admins_or_games() -> None:
    status = _status_service()
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config)
    texts = "\n".join(_iter_texts(layout.to_components()))
    assert "Admins" not in texts, "no admins line when no operator is configured"
    assert "*(none configured)*" in texts


def test_layout_sections_and_separator_structure() -> None:
    """A release deploy: Version vX.Y.Z headlines as a Section; the Bot
    section (uptime, admins, games) sits between Infra and the footer."""
    status = _status_service(
        deploy_label="v0.1.0",
        deploy_kind="release",
        deploy_ref="v0.1.0",
        deploy_commit="9f8e7d6c5b4a3928173645508174938271626153",
        deploy_url="https://github.com/merlin-pinpin-org/kingdoms-services/releases/tag/v0.1.0",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="prod")
    top = layout.to_components()
    assert len(top) == 1 and top[0]["type"] == TYPE_CONTAINER
    kinds = [c["type"] for c in top[0]["components"]]
    assert kinds.count(TYPE_TEXT_DISPLAY) >= 2
    assert kinds.count(TYPE_SECTION) == 1, "the release headline is a Section"
    texts = _iter_texts(top)
    assert any("Version v0.1.0" in text for text in texts), "releases headline as Version vX.Y.Z"
    assert any(text.startswith("**Bot**") for text in texts), "the Bot section is present"
    section = next(c for c in top[0]["components"] if c["type"] == TYPE_SECTION)
    assert section["accessory"]["label"] == "🔗 v0.1.0"


def test_release_renders_tag_and_commit_as_distinct_lines() -> None:
    """A release deploy shows the tag AND the commit it points at —
    the tag never renders as the commit (the tree URL's last segment
    is the tag, not the sha)."""
    status = _status_service(
        deploy_label="v0.2.2",
        deploy_kind="release",
        deploy_ref="v0.2.2",
        deploy_commit="9f8e7d6c5b4a3928173645508174938271626153",
        deploy_commit_ts="1790000000",
        deploy_tree_url="https://github.com/merlin-pinpin-org/kingdoms-services/tree/v0.2.2",
        deploy_branch="main",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    texts = _iter_texts(layout.to_components())
    identity = next(t for t in texts if "🔖 Tag" in t)
    lines = identity.splitlines()
    assert "🔖 Tag `v0.2.2`" in lines, "the tag rides on its own Tag line"
    assert "🔧 Commit `9f8e7d6` <t:1790000000:R>" in lines, "the resolved commit sha rides on the Commit line"
    assert not any("Commit `v0.2.2`" in text for text in texts), "the tag is never rendered as the commit"
    labels = [b["label"] for b in _iter_buttons(layout.to_components())]
    assert "🔖 Tag" in labels, "the release carries a Tag button"
    commit_button = next(b for b in _iter_buttons(layout.to_components()) if b["label"] == "🔧 Commit")
    assert commit_button["url"].endswith("/commit/9f8e7d6"), "the Commit button links the resolved sha"


def test_ci_job_line_sits_under_a_separator_above_the_image() -> None:
    """The CI job id line sits under a Separator, directly above the
    image line it built; the CI button links the services CI job."""
    digest64 = "0123456789abcdef" * 4
    status = _status_service(
        deploy_kind="pr",
        deploy_ref="42",
        deploy_image=f"ghcr.io/merlin-pinpin-org/kingdoms-services:pr-42-x@sha256:{digest64}",
        deploy_ci_run_id="987654",
        deploy_ci_run_number="321",
    )
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    kinds = [c["type"] for c in layout.to_components()[0]["components"]]
    texts = _iter_texts(layout.to_components())
    ci_line = next(t for t in texts if "Job CI" in t)
    assert "#321" in ci_line, "the CI job number headlines the Job CI line"
    image_line = next(t for t in texts if t.startswith("📦 Image"))
    assert texts.index(ci_line) < texts.index(image_line), "the Job CI line sits above the image line"
    assert kinds.count(TYPE_SEPARATOR) == 3, "Bot/Services, CI job (no deploy job data in this fixture)"
    ci_button = next(b for b in _iter_buttons(layout.to_components()) if b["label"] == "🧪 Job")
    assert ci_button["url"].endswith("/actions/runs/987654"), "the CI button links the CI job"


def test_layout_is_localized() -> None:
    status = _status_service(deploy_label="v0.1.0")
    config = AnnounceConfig(locale="fr", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="prod")
    joined = "\n".join(_iter_texts(layout.to_components()))
    assert "Kingdoms — Déploiement" in joined


def test_layout_falls_back_to_english_for_unknown_locale() -> None:
    status = _status_service(deploy_label="sha-abc1234")
    config = AnnounceConfig(locale="xx", config_dir=CONFIG_DIR)
    layout = build_announcement_layout(status, config, env="test")
    joined = "\n".join(_iter_texts(layout.to_components()))
    assert "Kingdoms — Deployment" in joined


@pytest.mark.asyncio
async def test_announce_delivers_start_layout_to_every_guild() -> None:
    status = _status_service(deploy_label="pr-42-x")
    logs = _FakeLogService()
    bot = _Bot(["111", "222"], latency=0.045)
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(bot, status, config, logs_service=logs, deploy_env="test")  # type: ignore[arg-type]
    assert set(logs.events) == {"111", "222"}
    for events in logs.events.values():
        assert len(events) == 1
        event = events[0]
        assert event.kind == "start"
        assert event.footer == ""
        assert not any("kingdoms-deploy" in t for t in _iter_texts(event.layout.to_components())), (
            "no machine footer in the layout"
        )
        assert isinstance(event.layout, discord.ui.LayoutView)
        joined = "\n".join(_iter_texts(event.layout.to_components()))
        assert "Latency: 45 ms" in joined, "the gateway latency rides in the Bot section"


@pytest.mark.asyncio
async def test_announce_disabled_silences_every_guild() -> None:
    status = _status_service(deploy_label="pr-42-x")
    logs = _FakeLogService()
    bot = _Bot(["111"])
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(  # type: ignore[arg-type]
        bot, status, config, logs_service=logs, deploy_env="ci", enabled=False
    )
    assert logs.events == {}


@pytest.mark.asyncio
async def test_announce_skipped_without_log_service() -> None:
    status = _status_service()
    bot = _Bot(["111"])
    config = AnnounceConfig(locale="en", config_dir=CONFIG_DIR)
    await announce_startup(bot, status, config, logs_service=None, deploy_env="test")  # type: ignore[arg-type]


def test_log_service_protocol_shape() -> None:
    """The fake used in tests satisfies the LogService call surface used here."""
    assert isinstance(_FakeLogService().log_event, object)
    service: Any = _FakeLogService()
    assert callable(service.log_event)


def test_lifecycle_event_is_a_plain_dataclass() -> None:
    event = LifecycleEvent(kind="start", message="m", footer="f")
    assert event.kind == "start"
    assert event.footer == "f"
    assert event.layout is None
    assert LogService is not None
