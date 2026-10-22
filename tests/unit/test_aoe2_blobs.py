"""Unit tests for the AoE2 payload blob decoder (slotinfo / options).

The blobs are base64-encoded zlib; the decompressed bytes follow the
game's binary encodings (community-reverse-engineered, see the blobs
module docstring for the references). The decoder is the game-module
side of serving full MatchDetails from the provider payloads.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest

from kingdoms.core.games.aoe2.blobs import (
    BlobDecodeError,
    decode_blob,
    decode_options,
    decode_slotinfo,
)


def _encode(raw: bytes) -> str:
    """Encode raw decompressed bytes the way the AoE2 APIs do."""
    return base64.b64encode(zlib.compress(raw)).decode()


def _slotinfo(slots: list[dict[str, object]]) -> str:
    """Build a real-shape slotinfo blob: base64(zlib("<N>,<json>"))."""
    return _encode(f"{len(slots)},{json.dumps(slots)}".encode())


def _options(pairs: list[tuple[str, str]]) -> str:
    """Build a real-shape options blob.

    Decompressed: a base64 string; its bytes are one entry-count byte
    followed by uint32 LE length-prefixed "<key>:<value>" records —
    the double-base64 format confirmed against the real payloads.
    """
    records = [f"{k}:{v}" for k, v in pairs]
    stream = bytes([len(records)]) + b"".join(struct.pack("<I", len(r.encode())) + r.encode() for r in records)
    return _encode(base64.b64encode(stream))


def test_decode_slotinfo_real_shape() -> None:
    """A slotinfo blob decodes to its slot list (probe payload shape)."""
    slots = [
        {"profileInfo.id": 25252491, "stationID": 1, "teamID": 0, "factionID": 0, "isReady": 1},
        {"profileInfo.id": -1, "stationID": -1, "teamID": -1, "factionID": -1, "isReady": 0},
    ]
    assert decode_slotinfo(_slotinfo(slots)) == slots


def test_decode_options_real_shape() -> None:
    """An options blob decodes to its key:value pairs (probe shape)."""
    blob = _options([("61", "-1"), ("0", "0"), ("62", "n"), ("92", "30"), ("87", "y")])
    assert decode_options(blob) == {"61": "-1", "0": "0", "62": "n", "92": "30", "87": "y"}


def test_decode_blob_dispatches_by_format() -> None:
    """decode_blob picks slotinfo for "<N>," payloads, options else."""
    slots = [{"profileInfo.id": 1, "factionID": 9}]
    assert decode_blob(_slotinfo(slots)) == slots
    assert decode_blob(_options([("10", "9")])) == {"10": "9"}


def test_decode_options_garbage_degrades_to_empty() -> None:
    """A truncated options stream keeps the cleanly parsed records."""
    blob = _encode(base64.b64encode(b"\x01\x00\x00\x00\x00garbage-tail").decode().encode())
    assert decode_options(blob) in ({}, {"": "\x00garbage-tail"})


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


def test_decode_slotinfo_not_json_raises() -> None:
    """A slotinfo payload whose JSON part is broken fails typed."""
    with pytest.raises(BlobDecodeError, match="not JSON"):
        decode_slotinfo(_encode(b"3,not-json"))
