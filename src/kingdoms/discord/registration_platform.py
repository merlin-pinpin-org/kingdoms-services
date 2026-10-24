"""Discord-side adapters for the registration stack (kingdoms-services#133).

Production wiring of the RegistrationService seams: the Mongo adapter
for the game-side profile bindings, and the AoE2 profile-validation
seam — a profile id is valid when the provider (ext-librematch via the
kingdoms.v1.Game contract) can resolve its stats.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from kingdoms.core.services.registration import (
    PROFILE_BINDINGS_COLLECTION,
)

INSIGHTS_BASE_URL = "https://www.aoe2insights.com"
INSIGHTS_URL_RE = re.compile(
    r"^https?://(?:www\.)?aoe2insights\.com/user/(\d{1,15})(?:[/?#].*)?$", re.IGNORECASE
)
_NUMERIC_ID_RE = re.compile(r"^\d{1,15}$")


def extract_insights_user_id(typed: str) -> str | None:
    """Extract the aoe2insights user id from a pasted URL, strictly.

    Only an aoe2insights.com URL of the exact shape
    ``https://www.aoe2insights.com/user/<digits>...`` matches — anything
    else (another host, a path-injection attempt, a query trick) is
    rejected. Double gate: the anchored regex AND a parsed-URL host check
    (the id never leaves the regex's capture group, so the downstream
    request URL cannot be tampered with).
    """
    match = INSIGHTS_URL_RE.match(typed)
    if match is None:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(typed)
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in (
        "www.aoe2insights.com",
        "aoe2insights.com",
    ):
        return None
    return match.group(1)


async def resolve_relic_id(
    typed_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str | None:
    """Resolve whatever id the user typed into the relic profile id.

    The user may paste either id (the 2026-10-16 UX lesson: the insights
    URL id and the relic id are different numbers and only the relic id
    works with librematch/aoe2lobby):

    - a relic id (what we want) — validates as-is;
    - an aoe2insights user id — resolved through
      ``/user/<id>/relic-id/``, which answers the relic id in plain text;
    - a full insights URL — **always resolved**: the user id is extracted
      by the strict URL pattern, then mapped to the relic id.

    Injection safety: the request URL is built ONLY from a
    regex-validated digit string (<= 15 chars); the reply must be a
    bare numeric id — any HTML, extra text or mismatch is rejected.

    ``transport`` is the test seam; None means the real network.
    Returns None when nothing resolves (unknown id, insights down).
    """
    typed = typed_id.strip()
    if not typed or len(typed) > 100:
        return None
    # A pasted insights URL ALWAYS resolves: the id in the URL is the
    # insights user id, never the relic id.
    url_id = extract_insights_user_id(typed)
    if url_id is not None:
        typed = url_id
    elif not _NUMERIC_ID_RE.fullmatch(typed):
        return None
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True, transport=transport) as client:
            reply = await client.get(f"{INSIGHTS_BASE_URL}/user/{typed}/relic-id/")
            if reply.status_code != 200:
                return None
            text = reply.text.strip()
    except Exception:
        logging.getLogger("kingdoms.registration.seam").warning(
            "INSIGHTS relic-id resolve failed (id %s)", typed, exc_info=True
        )
        return None
    # The reply must be a bare numeric id (the relic-id page answers
    # plain text); anything else — HTML, an error page, an injection —
    # is rejected. A reply equal to the typed id means the typed id was
    # already the relic id: not a resolution.
    if not _NUMERIC_ID_RE.fullmatch(text) or text == typed:
        return None
    return text


class MongoRegistrationDatabase:
    """Async MongoDB adapter for the RegistrationService persistence seam."""

    def __init__(self, database: Any) -> None:
        """Wrap an async MongoDB database (``get_async_database``)."""
        self._database = database

    async def upsert_entry(self, collection: str, document: dict[str, Any]) -> None:
        """Insert or replace one document by ``_id``."""
        await self._database[collection].replace_one({"_id": document["_id"]}, document, upsert=True)

    async def find_entry(self, collection: str, entry_id: str) -> dict[str, Any] | None:
        """Return one document by ``_id``; None when absent."""
        doc = await self._database[collection].find_one({"_id": entry_id})
        return dict(doc) if doc is not None else None

    async def find_user_bindings(self, user_id: str) -> list[dict[str, Any]]:
        """List the game-profile bindings of a user (all games)."""
        cursor = self._database[PROFILE_BINDINGS_COLLECTION].find({"user_id": user_id})
        return [dict(doc) async for doc in cursor]

    async def find_binding_by_profile(self, game_key: str, profile_id: str) -> dict[str, Any] | None:
        """Return the binding owning a profile; None when unbound."""
        doc = await self._database[PROFILE_BINDINGS_COLLECTION].find_one(
            {"game_key": game_key, "profile_id": profile_id}
        )
        return dict(doc) if doc is not None else None

    async def delete_binding(self, game_key: str, user_id: str, profile_id: str) -> bool:
        """Drop one profile binding; True when one existed."""
        result = await self._database[PROFILE_BINDINGS_COLLECTION].delete_one(
            {"_id": f"binding:{game_key}:{user_id}:{profile_id}"}
        )
        return bool(result.deleted_count > 0)

    async def list_bindings_for_game(self, game_key: str) -> list[dict[str, Any]]:
        """List every profile binding of a game (live dashboard reuse, #147)."""
        cursor = self._database[PROFILE_BINDINGS_COLLECTION].find({"game_key": game_key})
        return [dict(doc) async for doc in cursor]


def _site_of(site_key: str) -> Any:
    """Return one declared site by key (the registry lookup)."""
    from kingdoms.core.services.profile_sites import profile_sites_registry

    return profile_sites_registry().site("aoe2", site_key)


class ProviderStatsUnavailableError(RuntimeError):
    """The provider declares no player-stats capability (configuration gap)."""


class Aoe2ProfileValidationSeam:
    """AoE2 profile validation through the provider (kingdoms.v1.Game).

    A profile id is valid when the configured provider can resolve its
    stats. With no provider URI (local runs), every id is rejected —
    validation is the whole point of the binding (reference §0/§6).
    """

    DEFAULT_PROVIDER_URI = "kingdoms-ext-librematch:50061"

    def __init__(self, provider_uri: str | None = None) -> None:
        """Read the provider address from the environment by default.

        The docker-network default (kingdoms-ext-librematch:50061) stands
        in when the environment declares no URI: the compose network
        always carries the provider, and an empty URI made the seam
        reject every profile silently (the 2026-10-16 test findings).
        """
        self._provider_uri = provider_uri or os.environ.get("EXT_LIBREMATCH_URI", "") or self.DEFAULT_PROVIDER_URI
        logging.getLogger("kingdoms.registration.seam").info(
            "AOE2 PROFILE SEAM provider_uri=%s", self._provider_uri
        )

    async def validate_profile(self, profile_id: str) -> dict[str, Any] | None:
        """Return the profile's public data when the provider knows it.

        The typed id may be either the relic id (what the provider wants)
        or the aoe2insights user id (what the user pastes). The provider
        is tried first; on a miss, the insights resolver maps the typed
        id to the relic id and the validation retries \u2014 the binding
        always stores the relic id.
        """
        from kingdoms.core.rpc.game_client import GameProviderClient

        client = GameProviderClient(self._provider_uri, "ext-librematch", "aoe2")
        capabilities = await client.get_capabilities()
        if not capabilities.player_stats:
            raise ProviderStatsUnavailableError(
                "ext-librematch ne sert pas les stats de profil "
                "(provider mal configur\u00e9 ou injoignable \u2014 vois les logs du bot)"
            )
        typed = profile_id.strip()
        # Any known site's URL parses through the profile-sites registry:
        # a relic-keyed site (aoe2.GG, ageofempires.com) carries the relic
        # id directly — no resolution needed; the insights URL keeps its
        # resolve path (its id is the site's own).
        from kingdoms.core.services.profile_sites import ID_KIND_RELIC, parse_profile_url

        parsed = parse_profile_url("aoe2", typed)
        if parsed is not None:
            _site_key, site_id = parsed
            site = _site_of(parsed[0])
            if site is not None and site.id_kind == ID_KIND_RELIC:
                return await self._validate_with_client(client, site_id)
        if extract_insights_user_id(typed) is not None:
            relic_id = await resolve_relic_id(typed)
            if relic_id is None:
                return None
            return await self._validate_with_client(client, relic_id)
        # A plain id: try the provider first (a relic id validates directly),
        # then resolve through insights (the user typed the insights id).
        validated = await self._validate_with_client(client, typed)
        if validated is not None:
            return validated
        relic_id = await resolve_relic_id(typed)
        if relic_id is None:
            return None
        return await self._validate_with_client(client, relic_id)

    @staticmethod
    async def _validate_with_client(client: Any, profile_id: str) -> dict[str, Any] | None:
        """Validate one relic id against the running provider."""
        stats = await client.get_player_stats(profile_id)
        if stats is None:
            return None
        entries = [e for b in stats.blocks for e in b.entries]
        aliases = [e.value for e in entries if e.key == "alias"]
        if not aliases:
            aliases = [e.value for e in entries][:5]
        # the profile name rides the binding (owner direction): the stored
        # name of each profile id, so every link renders it without a
        # provider round-trip
        return {
            "profile_id": profile_id,
            "profile_name": aliases[0] if aliases else "",
            "aliases": aliases[:5],
        }
