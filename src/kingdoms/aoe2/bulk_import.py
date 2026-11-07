"""Bulk Liquipedia map import: list in, maps out (transactional).

Each line is one map, accepted as: the map id (map:public:arena),
the English name with underscores (arena / Dry_River), or a full
Liquipedia URL. Every missing map is imported from Liquipedia and
associated; any failure rolls the whole batch back (#259 semantics).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_LIQUIPEDIA_URL_RE = re.compile(
    r"^https?://liquipedia\.net/ageofempires/(?:index\.php\?title=)?([A-Za-z0-9_()%.-]+)",
)


@dataclass
class BulkImportLine:
    """One parsed line: its raw text and the resolved page name."""

    raw: str
    page_name: str = ""
    error: str = ""


@dataclass
class BulkImportResult:
    """The batch outcome: created entries, or the first failure."""

    created: list[Any] = field(default_factory=list)
    failure: str = ""


def parse_import_line(raw: str) -> BulkImportLine:
    """Parse one import line into a page name (id / slug / URL)."""
    line = raw.strip()
    if not line:
        return BulkImportLine(raw="")
    if line.startswith("map:"):
        # map:public:arena -> arena (the slug is the page tail)
        parts = line.split(":")
        if len(parts) != 3 or not parts[2]:
            return BulkImportLine(raw=line, error=f"id invalide : {line}")
        return BulkImportLine(raw=line, page_name=parts[2].replace("_", " "))
    url_match = _LIQUIPEDIA_URL_RE.match(line)
    if url_match:
        page = url_match.group(1)
        return BulkImportLine(raw=line, page_name=page.replace("_", " "))
    if re.fullmatch(r"[A-Za-z0-9_ -]+", line):
        return BulkImportLine(raw=line, page_name=line.replace("_", " "))
    return BulkImportLine(raw=line, error=f"ligne non reconnue : {line}")
