"""AoE2 payload blob decoding: base64 + zlib (slotinfo / options).

The Worlds Edge Link payloads embed the real match parameters as
compressed blobs: base64-encoded zlib. They carry everything the
community sites show (aoe2insights) that the raw LibreMatch payload
does not decode: per-slot civs, teams, and the game options (map size,
speed, victory condition, ...).

This module is the archaic-but-necessary decoder. It is game-module
code, not core: only the decoded, structured result crosses into
``MatchDetails``.
"""

from __future__ import annotations

import base64
import binascii
import json
import zlib


class BlobDecodeError(ValueError):
    """The blob is not a decodable base64+zlib payload."""


def decode_blob(blob: str) -> dict[str, object]:
    """Decode a base64+zlib blob into its JSON object.

    Raises ``BlobDecodeError`` when the payload is absent, not valid
    base64, not zlib-compressed, or not a JSON object — the caller
    degrades to serving the raw blob alongside the match.
    """
    if not blob:
        raise BlobDecodeError("empty blob")
    try:
        raw = base64.b64decode(blob)
    except (binascii.Error, ValueError) as exc:
        raise BlobDecodeError(f"invalid base64: {exc}") from exc
    try:
        payload = zlib.decompress(raw)
    except zlib.error as exc:
        raise BlobDecodeError(f"invalid zlib stream: {exc}") from exc
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BlobDecodeError(f"decoded payload is not JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise BlobDecodeError("decoded payload is not an object")
    return decoded
