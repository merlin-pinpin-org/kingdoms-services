"""Unit tests for the opt-in debug capture (kingdoms-core)."""

from __future__ import annotations

import json

from kingdoms.core.debug import capture, debug_enabled


def test_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("KINGDOMS_DEBUG", raising=False)
    monkeypatch.delenv("KINGDOMS_DEBUG_FILE", raising=False)
    assert debug_enabled() is False
    capture("provider.fetch", answer={"a": 1})


def test_enabled_writes_json_line_to_file(monkeypatch, tmp_path) -> None:
    target = tmp_path / "debug.jsonl"
    monkeypatch.setenv("KINGDOMS_DEBUG", "1")
    monkeypatch.setenv("KINGDOMS_DEBUG_FILE", str(target))
    assert debug_enabled() is True
    capture("blob.decode_failed", blob_field="slotinfo", error="invalid base64")
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "blob.decode_failed"
    assert record["blob_field"] == "slotinfo"
    assert record["error"] == "invalid base64"
    assert "at" in record


def test_enabled_accepts_truthy_values(monkeypatch) -> None:
    monkeypatch.setenv("KINGDOMS_DEBUG", "true")
    assert debug_enabled() is True
    monkeypatch.setenv("KINGDOMS_DEBUG", "yes")
    assert debug_enabled() is True


def test_falsy_value_stays_disabled(monkeypatch) -> None:
    monkeypatch.setenv("KINGDOMS_DEBUG", "0")
    assert debug_enabled() is False
    monkeypatch.setenv("KINGDOMS_DEBUG", "")
    assert debug_enabled() is False
