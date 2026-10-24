"""Unit tests for the Discord emoji allowlist (the UI SDK registry)."""

from __future__ import annotations

from kingdoms.discord.ui.emoji import ALLOWED_CODEPOINTS, EMOJI, is_allowed


def test_semantic_constants_are_allowed() -> None:
    """Every registry constant is, by construction, allowlisted."""
    for name in dir(EMOJI):
        if name.startswith("_"):
            continue
        assert is_allowed(getattr(EMOJI, name)), name


def test_known_rejected_codepoints_are_not_allowed() -> None:
    """The live-rejected emoji (50035) stay out of the allowlist."""
    assert not is_allowed("oppers")  # guard-rail for the helper itself
    assert 0x1F5F2 not in ALLOWED_CODEPOINTS  # rejected live 2026-10-16
    assert 0x1F5AE not in ALLOWED_CODEPOINTS  # rejected live 2026-10-21
    assert 0x1F50C not in ALLOWED_CODEPOINTS  # rejected in CI 2026-10-22
    assert 0x1F534 not in ALLOWED_CODEPOINTS
    assert 0x1F50D not in ALLOWED_CODEPOINTS


def test_variation_selector_emojis_allowed() -> None:
    """Multi-codepoint emoji (base + VS16) validate through every part."""
    assert is_allowed("⚠️")
    assert is_allowed("🗺️")
