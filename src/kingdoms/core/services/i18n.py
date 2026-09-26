"""Message catalog: localized bot messages (guild and DM surfaces).

The catalog loads the ``locales/*.yaml`` files once and renders keys
per locale, with English as the universal fallback:

- **guild locale** — the global per-guild language (``/admin``), used
  for the channel messages: announcements, lifecycle events, panels;
- **user locale** — the per-user DM language (``/admin`` in DM), used
  for everything the bot sends to that user in a DM (error reports,
  enrollment flows, match reports). DMs follow the user, not a guild.

Missing keys degrade to English; missing files degrade to the built-in
fallbacks. The catalog never raises — a message always renders.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("kingdoms.core.i18n")

DEFAULT_LOCALE = "en"
FALLBACKS: dict[str, str] = {
    "lifecycle.stop": "Bot shutting down.",
    "lifecycle.policy.grant": "Access policy updated: role <@&{role_id}> granted view, by <@{by}>.",
    "lifecycle.policy.reset": "Access policy reset to admin-only, by <@{by}>.",
    "lifecycle.policy.route": "Bot logs routed to <#{channel_id}>{previous_note}, by <@{by}>.",
    "lifecycle.policy.visibility": "Bot logs visibility set to {state}, by <@{by}>.",
    "lifecycle.policy.language": "Language set to {lang}, by <@{by}>.",
}


class MessageCatalog:
    """Locale-aware message rendering over the yaml catalogs."""

    def __init__(self, config_dir: Path | str = "config") -> None:
        """Load every ``locales/*.yaml`` under the config directory."""
        self._sections: dict[str, dict[str, str]] = {}
        directory = Path(config_dir) / "locales"
        try:
            for path in sorted(directory.glob("*.yaml")):
                locale = path.stem
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                entries = data.get(locale, {})
                if isinstance(entries, dict):
                    self._sections[locale] = {
                        str(key): str(value) for key, value in entries.items() if isinstance(value, str)
                    }
        except Exception:
            logger.warning("LOCALE CATALOG LOAD FAILED (%s) — falling back to built-ins", directory)
            self._sections = {}

    def render(self, key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
        """Render a dotted key for a locale; English fallback, never raises."""
        template = self._lookup(key, locale) or self._lookup(key, DEFAULT_LOCALE) or FALLBACKS.get(key, key)
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template

    def _lookup(self, key: str, locale: str) -> str | None:
        """Resolve a dotted key inside one locale section, flattened."""
        section = self._sections.get(locale, {})
        if key in section:
            return section[key]
        current: Any = section
        for part in key.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        if isinstance(current, str):
            return current
        return None
