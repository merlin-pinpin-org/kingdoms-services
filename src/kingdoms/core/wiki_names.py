"""Wiki page naming — the shared util for any MediaWiki-style provider.

Page-name extraction from URLs and the title-casing variants a wiki
resolves case-sensitively. Extracted from the legacy in-process seed
so the ext providers import a PUBLIC util, never a private symbol
(architecture rule R1 — the audit's wiki provider → mapsdata.seed
private imports).
"""

from __future__ import annotations

DEFAULT_USER_AGENT_TEMPLATE = "KingdomsBot/1.0 (https://github.com/merlin-pinpin-org/kingdoms; {contact})"


def extract_page_name(name: str) -> str:
    """Extract the page title from a name or a full wiki URL.

    "https://<wiki-host>/<segment>/Team_Islands" -> "Team Islands";
    query strings and trailing paths drop.
    """
    name = name.strip()
    if "://" in name:  # a pasted wiki URL, not a bare name
        tail = name.split("/")[-1]
        tail = tail.split("?")[0].split("#")[0].strip("/")
        return tail.replace("_", " ")
    return name


def title_variants(name: str) -> list[str]:
    """Return the casings to try, in order: as-given, then title-cased.

    MediaWiki resolves page titles case-sensitively; a lowercase catalog
    name ("team islands") misses the cased wiki title ("Team Islands")
    — both casings are tried, first hit wins.
    """
    title = " ".join(w[:1].upper() + w[1:] for w in name.split())
    return [name] if name == title else [name, title]


def wiki_user_agent() -> str:
    """Build the identified User-Agent; the contact comes from config.

    The generic ``PROVIDER_CONTACT`` — every provider's operational
    address (the wiki terms of use demand an identified agent).
    """
    from kingdoms.core.upstream_identity import provider_contact

    contact = provider_contact()
    if not contact:
        return ""
    return DEFAULT_USER_AGENT_TEMPLATE.format(contact=contact)
