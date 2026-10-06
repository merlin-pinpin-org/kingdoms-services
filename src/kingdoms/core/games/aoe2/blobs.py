"""AoE2 payload blob decoding: base64 + zlib (slotinfo / options).

The Worlds Edge Link payloads embed the real match parameters as
compressed blobs: base64-encoded zlib. They carry everything the
community sites show (aoe2insights) that the raw LibreMatch payload
does not decode: per-slot civs, teams, and the game options (map size,
speed, victory condition, ...).

This module is the archaic-but-necessary decoder. It is game-module
code, not core: only the decoded, structured result crosses into
``MatchDetails``.

Format references (community reverse-engineering, none of it
documented by World's Edge):

* librematch-collector — slotinfo/options parsers, option id maps:
  https://github.com/librematch/librematch-collector
  (collector/src/parser/{slotinfo,options}.ts)
* aoe2-apis — live lobby payloads and the Worlds Edge Link community
  API: https://github.com/ustacode/aoe2-apis
  (docs/02-live-lobbies.md: "even two times Base64 encoded")
* AoE2Control GameOptions — in-game option enums (map size, ages,
  victory, civilizations, map ids):
  https://aoe2control.github.io/game-options/
* aoc-mgz — the .mgz replay header lobby struct, the other source of
  the same settings: https://github.com/happyleavesaoc/aoc-mgz
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
import zlib


class BlobDecodeError(ValueError):
    """The blob is not a decodable base64+zlib payload."""


def decompress_blob(blob: str) -> bytes:
    """Decompress a base64+zlib blob into its raw bytes.

    Raises ``BlobDecodeError`` when the payload is absent, not valid
    base64, or not a zlib stream — the caller degrades to serving the
    raw blob alongside the match.
    """
    if not blob:
        raise BlobDecodeError("empty blob")
    try:
        raw = base64.b64decode(blob)
    except (binascii.Error, ValueError) as exc:
        raise BlobDecodeError(f"invalid base64: {exc}") from exc
    try:
        return zlib.decompress(raw)
    except zlib.error as exc:
        raise BlobDecodeError(f"invalid zlib stream: {exc}") from exc


def decode_slotinfo(blob: str) -> list[dict[str, object]]:
    """Decode a slotinfo blob into its list of slot objects.

    The decompressed payload is ``"<N>,<json array>"``: a slot count,
    a comma, then the JSON array of slot records (the format the
    Worlds Edge API serializes for the in-game slots). Some slot
    values (``metaData``) are themselves base64-encoded twice.
    """
    payload = decompress_blob(blob).decode("utf-8", errors="strict")
    _, _, json_part = payload.partition(",")
    try:
        slots = json.loads(json_part)
    except json.JSONDecodeError as exc:
        raise BlobDecodeError(f"slotinfo payload is not JSON: {exc}") from exc
    if not isinstance(slots, list):
        raise BlobDecodeError("slotinfo payload is not an array")
    return slots


def decode_options(blob: str) -> dict[str, object]:
    """Decode an options blob into its ``key:value`` option pairs.

    The decompressed payload is a base64 string; the decoded bytes are
    a binary stream of records: ``uint32 LE length`` followed by ASCII
    ``"<key>:<value>"``. Option ids are variant-dependent (see
    ``options_map``).
    """
    inner = decompress_blob(blob).decode("ascii", errors="strict").strip('"')
    try:
        stream = base64.b64decode(inner)
    except (binascii.Error, ValueError) as exc:
        raise BlobDecodeError(f"invalid inner base64: {exc}") from exc
    options: dict[str, object] = {}
    offset = 1  # byte 0 is the entry count; records follow
    while offset + 4 <= len(stream):
        (record_len,) = struct.unpack_from("<I", stream, offset)
        offset += 4
        if record_len > 1024 or offset + record_len > len(stream):
            break
        record = stream[offset : offset + record_len].decode("ascii", errors="replace")
        offset += record_len
        key, _, value = record.partition(":")
        if key:
            options[key] = value
    return options


def decode_blob(blob: str) -> dict[str, object] | list[dict[str, object]]:
    """Decode one slotinfo or options blob, selecting the format.

    Slotinfo payloads start with ``"<N>,"``; options payloads with a
    base64 header. Returns the slot list (slotinfo) or the option
    pairs as a dict (options).
    """
    payload = decompress_blob(blob).decode("utf-8", errors="replace")
    if payload[:1].isdigit() and "," in payload[:8]:
        return decode_slotinfo(blob)
    return decode_options(blob)
