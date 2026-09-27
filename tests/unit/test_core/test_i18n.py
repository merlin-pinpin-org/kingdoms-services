"""Unit tests for the MessageCatalog (kingdoms-services#110, #115).

The catalog must load the nested ``admin:`` section of the yaml
locales as dotted keys (``admin.title``) \u2014 without the flattening,
every admin key fell back to English (the \"no translation in /admin\"
bug). The lifecycle fallbacks ride as built-ins when a key is missing.
"""

from __future__ import annotations

from pathlib import Path

from kingdoms.core.services.i18n import DEFAULT_LOCALE, FALLBACKS, MessageCatalog

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def test_catalog_loads_the_real_locales() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.render("admin.title", "en") == "Kingdoms — Admin"
    assert catalog.render("admin.title", "fr") == "Kingdoms — Admin"
    assert catalog.render("admin.language", "fr") == "Langue de la guilde"


def test_nested_sections_flatten_to_dotted_keys() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    fr = catalog._sections["fr"]
    assert fr["admin.title"], "the nested admin section flattens to admin.title"
    assert fr["admin.visibility_admin_only"], "deeply nested keys flatten too"
    assert "admin" not in fr or isinstance(fr["admin"], str)


def test_render_falls_back_to_english_for_missing_fr_keys() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    rendered = catalog.render("admin.dm_title", "fr")
    assert rendered == "Kingdoms — Admin (DM)"


def test_render_degrades_to_the_key_for_unknown_entries() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.render("admin.unknown_key", "fr") == "admin.unknown_key"


def test_render_formats_placeholders() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    rendered = catalog.render("admin.language_hint", "fr", value="Français")
    assert "Français" in rendered
    assert rendered == catalog.render("admin.language_hint", "fr", value="Français")


def test_lifecycle_fallbacks_ride_as_builtins() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.render("lifecycle.stop", "en") == FALLBACKS["lifecycle.stop"]


def test_missing_directory_degrades_gracefully() -> None:
    catalog = MessageCatalog(Path("tests/unit/test_core/__nonexistent__"))
    assert catalog.render("admin.title", "fr") == "admin.title"
    assert catalog.render("lifecycle.stop", DEFAULT_LOCALE) == FALLBACKS["lifecycle.stop"]


def test_announce_keys_render_per_locale() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.render("announce.services_label", "fr") == "Services"
    assert catalog.render("announce.mods_label", "fr") == "Mods"


def test_section_resolves_one_prefix_with_english_fill() -> None:
    """section() is the one-loader contract: the announce strings come
    from the same catalog as /admin — no per-module yaml parsing."""
    catalog = MessageCatalog(CONFIG_DIR)
    announce_fr = catalog.section("announce", "fr")
    assert announce_fr["title"] == "Kingdoms — Déploiement"
    assert announce_fr["services_label"] == "Services"
    assert announce_fr["branch_label"] == "Branche"


def test_section_fills_missing_locale_keys_with_english() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    announce_en = catalog.section("announce", "en")
    announce_fr = catalog.section("announce", "fr")
    assert set(announce_fr) == set(announce_en), "both locales expose the same keys"
    assert announce_fr["ci_label"] == "Job"


def test_section_of_unknown_prefix_is_empty() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.section("nope", "fr") == {}


def test_guards_denied_renders_per_locale() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert "r\u00e9serv\u00e9e aux admins" in catalog.render("guards.denied", "fr")
    assert "reserved for bot admins" in catalog.render("guards.denied", "en")


def test_admin_visibility_options_render_per_locale() -> None:
    catalog = MessageCatalog(CONFIG_DIR)
    assert catalog.render("admin.visibility_admin_label", "fr") == "Admins uniquement"
    assert catalog.render("admin.visibility_public_hint", "fr") == "Tout le monde peut lire les logs"
    assert catalog.render("admin.channel_bot_logs", "fr") == "Logs du bot"
    assert catalog.render("admin.channel_bot_admins", "fr") == "Admins du bot"
