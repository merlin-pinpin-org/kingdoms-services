"""Unit tests for the AoE2 payload blob decoder (slotinfo / options).

The blobs are base64-encoded zlib; the decoder is the game-module side
of serving full MatchDetails from the provider payloads.
"""

from __future__ import annotations

import base64
import json
import zlib

import pytest

from kingdoms.core.games.aoe2.blobs import BlobDecodeError, decode_blob


def _encode(payload: dict[str, object]) -> str:
    """Encode a payload the way the AoE2 APIs do: base64(zlib(json))."""
    return base64.b64encode(zlib.compress(json.dumps(payload).encode())).decode()


def test_decode_blob_round_trip() -> None:
    """A base64+zlib slotinfo payload decodes to its JSON object."""
    payload = {
        "slots": [
            {"slot_index": 0, "civ": 1, "team": 1},
            {"slot_index": 1, "civ": 7, "team": 2},
        ],
        "options": {"map_size": 2, "speed": 2, "victory": 1},
    }
    assert decode_blob(_encode(payload)) == payload


def test_decode_blob_minimal_payload() -> None:
    """An empty-ish object decodes cleanly."""
    assert decode_blob(_encode({"players": 2})) == {"players": 2}


def test_decode_blob_empty_raises() -> None:
    """An absent blob fails fast with the typed error."""
    with pytest.raises(BlobDecodeError, match="empty blob"):
        decode_blob("")


def test_decode_blob_invalid_base64_raises() -> None:
    """Non-base64 input fails with the typed error."""
    with pytest.raises(BlobDecodeError, match="invalid base64"):
        decode_blob("not-base64!!")


def test_decode_blob_not_zlib_raises() -> None:
    """Valid base64 but not a zlib stream fails with the typed error."""
    with pytest.raises(BlobDecodeError, match="invalid zlib"):
        decode_blob(base64.b64encode(b"plain text, no zlib").decode())


def test_decode_blob_not_json_raises() -> None:
    """A zlib stream that is not JSON fails with the typed error."""
    raw = zlib.compress(b"\x00\x01binary garbage")
    with pytest.raises(BlobDecodeError, match="not JSON"):
        decode_blob(base64.b64encode(raw).decode())


def test_decode_blob_non_object_json_raises() -> None:
    """A JSON array (not an object) fails with the typed error."""
    raw = zlib.compress(b"[1, 2, 3]")
    with pytest.raises(BlobDecodeError, match="not an object"):
        decode_blob(base64.b64encode(raw).decode())
