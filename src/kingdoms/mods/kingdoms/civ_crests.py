"""Kingdoms mod — civilization crest images (civ-crests-v1, Drasah 20/10).

The mod's player-facing civ lists (the 📜 Alliances pinned draft starter,
the pick & ban REVEAL DM, the battle-room meeting message) read as bare
names. Drasah's 2026-10-20 report: the platform database carries each
civilization's blason (``faction:aoe2:<Civ>`` content docs →
``image_url``), and the texts should wear them.

Discord cannot inline a remote image inside a line of text, so the
crests ride as ATTACHED files: they display under the message body, in
the same order as the names. The PNGs come from the licence-clean
aoe2techtree repository — the very URL the content docs store — so no
new upstream is introduced.

The mod's catalog keys are Drasah's French slugs (``azteques``) while
the dataset and the content docs speak the provider's keys
(``Aztecs``); :data:`_CIV_CREST_PROVIDER_KEYS` bridges the two. A
missing mapping or a failed download is cosmetic-only: the message
goes out regardless, minus that crest.
"""
from __future__ import annotations

import base64
import io
import logging
import struct
import zlib
from typing import Any

import discord
import httpx

logger = logging.getLogger("kingdoms.kingdoms.civ_crests")

CREST_BASE_URL = "https://raw.githubusercontent.com/SiegeEngineers/aoe2techtree/master/img/Civs"
MAX_ATTACHMENTS = 10

_CIV_CREST_PROVIDER_KEYS: dict[str, str] = {
    "azteques": "Aztecs",
    "bengalis": "Bengalis",
    "bourguignons": "Burgundians",
    "britanniques": "Britons",
    "celtes": "Celts",
    "ethiopiens": "Ethiopians",
    "georgiens": "Georgians",
    "huns": "Huns",
    "incas": "Incas",
    "italiens": "Italians",
    "japonais": "Japanese",
    "khmers": "Khmer",
    "malais": "Malay",
    "maliens": "Malians",
    "mapuche": "Mapuche",
    "mayas": "Mayans",
    "mongols": "Mongols",
    "perses": "Persians",
    "polonais": "Poles",
    "sarrasins": "Saracens",
    "siciliens": "Sicilians",
    "slaves": "Slavs",
    "danois": "Danes",
    "varegues": "Varangians",
    "saxons": "Saxons",
    "tupi": "Tupi",
    "vikings": "Vikings",
    "muisca": "Muisca",
    "armeniens": "Armenians",
    "dravidiens": "Dravidians",
    "berberes": "Berbers",
    "coumans": "Cumans",
    "espagnols": "Spanish",
    "francs": "Franks",
    "jurchen": "Jurchens",
    "khitan": "Khitans",
    "tatars": "Tatars",
    "teutons": "Teutons",
    "goths": "Goths",
    "hindoustanis": "Hindustanis",
    "bulgares": "Bulgarians",
    "lituaniens": "Lithuanians",
    "gurjaras": "Gurjaras",
    "coreens": "Koreans",
    "byzantins": "Byzantines",
    "romains": "Romans",
    "turcs": "Turks",
    "chinois": "Chinese",
    "shu": "Shu",
    "wei": "Wei",
    "wu": "Wu",
    "magyars": "Magyars",
    "birmans": "Burmese",
    "bohemiens": "Bohemians",
    "portugais": "Portuguese",
}

# alliances-lisibilite-v1 T2 (Drasah 25/10): unmapped civs keep a
# visual slot instead of dropping out of the crest row.
DEFAULT_CREST_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAABVUlEQVR42u3bOw7CMBAE0JyBgoqKihvQcmOuQUOHuA5ISEiAEn9Y787YmWIbunlxHBvtTpvt4bHmmgQgAAF8/XC53oevLMDtfBy2BCAAAQhAAAIQgAAEIAABLAPs9qdhSwACEIAPQGrjWc0K6AHB/RVgRwjZA5gRwjZBVoTQrwAjQvhnkA0Bcg5gQoAdhFgQoCdBBgT4URiNQHEXQCLQXIZQCFS3QQQC3XU4GoHy/4BIhCzAyAi/4akAIhDoAbwRugDwRCgGGBFhLjw1QGuEaoCREJbCFzVK9o6QCt8NgAXBDNAzQi58Va9wbwgl4aubpd+9tswIc/3AzbvFWRFqgpvb5dkQ/glvnhdgeCVql7zLwAQKwRK8+cRI5GqwPnXXkRlPiJbB3WeGWkJ4BA8bmrJAeAYPnxqrgYgIDhubS0FEBofPDX5CIILTDE6igmtyVAACeNUT/ptxBY9JPcQAAAAASUVORK5CYII="
)
_DEFAULT_CREST_BYTES = base64.b64decode(DEFAULT_CREST_B64)

_CREST_CACHE: dict[str, bytes] = {}


def civ_crest_url(civ_key: str) -> str | None:
    """Return the crest image URL of one mod civilization key (None unknown)."""
    provider_key = _CIV_CREST_PROVIDER_KEYS.get(str(civ_key).strip().lower())
    if provider_key is None:
        return None
    return f"{CREST_BASE_URL}/{provider_key.lower()}.png"


def civ_provider_name(civ_key: str) -> str | None:
    """Return the techtree provider key of one mod civ (None unknown).

    alliances-lisibilite-v1 T2 (Drasah 25/10): the 🔤 Noms answer shows
    the display name ↔ provider name pairing so Drasah can map the
    catalog slugs to the aoe2techtree dataset.
    """
    return _CIV_CREST_PROVIDER_KEYS.get(str(civ_key).strip().lower())


async def _crest_bytes(url: str) -> bytes | None:
    """Download one crest PNG (best effort, in-memory cached).

    The cache keeps the provisioning ticks cheap: the same civ list
    re-announced after a restart or a refresh never re-downloads.
    Tests monkeypatch this seam to avoid real network traffic.
    """
    cached = _CREST_CACHE.get(url)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.content
    except Exception:
        logger.warning("CIV CRESTS: download failed (%s)", url, exc_info=True)
        return None
    _CREST_CACHE[url] = data
    return data


async def civ_crest_files(civ_keys: Any) -> list[discord.File]:
    """Build the crest attachments for one civ list (order preserved).

    Unknown keys, download failures and the 10-attachment Discord cap
    degrade silently: the message always goes out, dressed with what
    could be fetched.
    """
    files: list[discord.File] = []
    seen: set[str] = set()
    for civ_key in civ_keys or ():
        key = str(civ_key).strip()
        if not key or key in seen or len(files) >= MAX_ATTACHMENTS:
            continue
        seen.add(key)
        data = _DEFAULT_CREST_BYTES
        filename = f"{key}.png"
        url = civ_crest_url(key)
        if url is not None:
            downloaded = await _crest_bytes(url)
            if downloaded is not None:
                data = downloaded
        files.append(discord.File(io.BytesIO(data), filename=filename))
    return files

# alliances-lisibilite-v1 T3 (Drasah 25/10): the Alliances surfaces now
# wear ONE composite mosaic instead of N full-size attachments — the
# page fits again, the crests stay. The mosaic is painted in PURE
# PYTHON (the scriptorium_art precedent): each crest PNG is decoded,
# resampled to its tile and composited on a transparent grid, and a
# small ORIGIN BADGE in the tile's corner carries the acquisition
# provenance — mosaic-emoji-badge (Drasah 26/10): the mark is the
# ORIGIN EMOJI itself, a Twemoji glyph composited in pure Python;
# the colour square survives only as the offline fallback.
MOSAIC_TILE = 112
MOSAIC_CREST = 104
MOSAIC_TILE_PAD = 4
MOSAIC_COLS = 5
MOSAIC_BADGE = 28
MOSAIC_BADGE_INSET = 2

ORIGIN_BADGE_COLORS: dict[str, tuple[int, int, int]] = {
    "starter": (245, 246, 248),
    "condition": (32, 166, 160),
    "marriage": (232, 112, 140),
    "royal": (241, 196, 15),
    "mercenary": (210, 180, 140),
}
DEFAULT_BADGE_COLOR = (130, 134, 140)

# mosaic-emoji-badge (Drasah 26/10): one Twemoji glyph per origin
# rides the tile's corner (72px source, resampled to the badge box).
ORIGIN_EMOJI_CODES: dict[str, str] = {
    "starter": "1f3b2",
    "condition": "1f5fa",
    "marriage": "1f48d",
    "royal": "1f451",
    "mercenary": "1f4dc",
}
DEFAULT_EMOJI_CODE = "1f91d"  # 🤝 default / earlier season
TWEMOJI_URL = (
    "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0"
    "/assets/72x72/{0}.png"
)
_EMOJI_CACHE: dict[str, bytes | None] = {}


async def _emoji_bytes(code: str) -> bytes | None:
    """Fetch (and cache) one Twemoji glyph PNG — None on failure."""
    if code in _EMOJI_CACHE:
        return _EMOJI_CACHE[code]
    data = await _crest_bytes(TWEMOJI_URL.format(code))
    _EMOJI_CACHE[code] = data
    return data


def _badge_border(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Darken one badge fill into its border colour."""
    return (
        max(0, int(color[0] * 0.45)),
        max(0, int(color[1] * 0.45)),
        max(0, int(color[2] * 0.45)),
    )


def _decode_png_rgba(data: bytes) -> tuple[int, int, bytes] | None:
    """Decode one 8-bit non-interlaced PNG into RGBA bytes (None = unsupported).

    The dataset crests are uniform (104×104, 8-bit palette + tRNS), but
    the decoder also accepts the 0/2/4/6 colour types so a provider swap
    never blanks the mosaic. Everything runs on zlib + struct — no
    imaging dependency exists in the bot image.
    """
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = height = 0
    bit_depth = color_type = interlace = 0
    palette = b""
    trns = b""
    idat = bytearray()
    offset = 8
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        payload = bytes(data[offset + 8 : offset + 8 + length])
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
        elif kind == b"PLTE":
            palette = payload
        elif kind == b"tRNS":
            trns = payload
        elif kind == b"IDAT":
            idat += payload
        elif kind == b"IEND":
            break
    if not width or not height or bit_depth != 8 or interlace:
        return None
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None
    stride = width * channels
    if len(raw) < (stride + 1) * height:
        return None
    out = bytearray(width * height * 4)
    position = 0
    previous = bytearray(stride)
    for y in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position : position + stride])
        position += stride
        if filter_type == 1:
            for x in range(channels, stride):
                line[x] = (line[x] + line[x - channels]) & 0xFF
        elif filter_type == 2:
            for x in range(stride):
                line[x] = (line[x] + previous[x]) & 0xFF
        elif filter_type == 3:
            for x in range(stride):
                left = line[x - channels] if x >= channels else 0
                line[x] = (line[x] + ((left + previous[x]) >> 1)) & 0xFF
        elif filter_type == 4:
            for x in range(stride):
                above = line[x - channels] if x >= channels else 0
                before = previous[x]
                corner = previous[x - channels] if x >= channels else 0
                paeth = above + before - corner
                distance_a = abs(paeth - above)
                distance_b = abs(paeth - before)
                distance_c = abs(paeth - corner)
                predictor = above if (distance_a <= distance_b and distance_a <= distance_c) else (
                    before if distance_b <= distance_c else corner
                )
                line[x] = (line[x] + predictor) & 0xFF
        previous = line
        for x in range(width):
            base = x * channels
            red = green = blue = alpha = 0
            if color_type == 6:
                red, green, blue, alpha = line[base], line[base + 1], line[base + 2], line[base + 3]
            elif color_type == 2:
                red, green, blue, alpha = line[base], line[base + 1], line[base + 2], 255
            elif color_type == 4:
                red, green, blue, alpha = line[base], line[base], line[base], line[base + 1]
            elif color_type == 0:
                red, green, blue, alpha = line[base], line[base], line[base], 255
            else:
                index = line[base]
                if index * 3 + 2 < len(palette):
                    red = palette[index * 3]
                    green = palette[index * 3 + 1]
                    blue = palette[index * 3 + 2]
                alpha = trns[index] if index < len(trns) else 255
            dest = (y * width + x) * 4
            out[dest], out[dest + 1], out[dest + 2], out[dest + 3] = red, green, blue, alpha
    return width, height, bytes(out)


def _encode_png_rgba(width: int, height: int, pixels: bytes) -> bytes:
    """Encode RGBA pixels as one filter-0 PNG (pure Python, the art precedent)."""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride : (y + 1) * stride]

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _resample_rgba(width: int, height: int, pixels: bytes, target_w: int, target_h: int) -> bytes:
    """Nearest-neighbour resample of RGBA pixels (crests are flat art)."""
    out = bytearray(target_w * target_h * 4)
    for y in range(target_h):
        src_y = min(height - 1, y * height // target_h)
        for x in range(target_w):
            src_x = min(width - 1, x * width // target_w)
            source = (src_y * width + src_x) * 4
            dest = (y * target_w + x) * 4
            out[dest : dest + 4] = pixels[source : source + 4]
    return bytes(out)


def _paste(
    canvas: bytearray, canvas_w: int, x: int, y: int, width: int, height: int, pixels: bytes
) -> None:
    """Blit RGBA pixels onto the canvas at (x, y) (source-over on flat art)."""
    stride = width * 4
    for row in range(height):
        dest = ((y + row) * canvas_w + x) * 4
        canvas[dest : dest + stride] = pixels[row * stride : (row + 1) * stride]


def _paste_alpha(
    canvas: bytearray, canvas_w: int, x: int, y: int, width: int, height: int, pixels: bytes
) -> None:
    """Alpha-composite RGBA pixels onto the canvas (source-over)."""
    for row in range(height):
        base = ((y + row) * canvas_w + x) * 4
        for col in range(width):
            src = (row * width + col) * 4
            dest = base + col * 4
            alpha = pixels[src + 3]
            if alpha == 255:
                canvas[dest : dest + 4] = pixels[src : src + 4]
            elif alpha:
                inv = 255 - alpha
                for i in range(3):
                    canvas[dest + i] = (
                        pixels[src + i] * alpha + canvas[dest + i] * inv + 127
                    ) // 255


def _draw_badge(canvas: bytearray, canvas_w: int, tile_x: int, tile_y: int, origin: str) -> None:
    """Stamp the FALLBACK colour badge in the tile's bottom-right corner."""
    fill = ORIGIN_BADGE_COLORS.get(origin, DEFAULT_BADGE_COLOR)
    border = _badge_border(fill)
    badge_x = tile_x + MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET
    badge_y = tile_y + MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET
    for y in range(MOSAIC_BADGE):
        for x in range(MOSAIC_BADGE):
            on_edge = x < 2 or y < 2 or x >= MOSAIC_BADGE - 2 or y >= MOSAIC_BADGE - 2
            color = border if on_edge else fill
            dest = ((badge_y + y) * canvas_w + badge_x + x) * 4
            canvas[dest : dest + 3] = bytes(color)
            canvas[dest + 3] = 255


def _draw_emoji_badge(
    canvas: bytearray, canvas_w: int, tile_x: int, tile_y: int, glyph: bytes | None
) -> bool:
    """Stamp the ORIGIN EMOJI in the tile's corner; False → colour fallback."""
    if glyph is None:
        return False
    decoded = _decode_png_rgba(glyph)
    if decoded is None:
        return False
    glyph_w, glyph_h, glyph_pixels = decoded
    badge = _resample_rgba(glyph_w, glyph_h, glyph_pixels, MOSAIC_BADGE, MOSAIC_BADGE)
    _paste_alpha(
        canvas,
        canvas_w,
        tile_x + MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET,
        tile_y + MOSAIC_TILE - MOSAIC_BADGE - MOSAIC_BADGE_INSET,
        MOSAIC_BADGE,
        MOSAIC_BADGE,
        badge,
    )
    return True


async def civ_crest_mosaic_file(
    entries: Any, filename: str = "alliances.png"
) -> discord.File | None:
    """Compose the hand's crests into ONE mosaic attachment (None = empty hand).

    ``entries`` pairs each civ key with its acquisition origin; every
    tile wears its crest and its ORIGIN EMOJI badge (colour square
    fallback offline), in hand order, five
    tiles per row. An undecodable or missing crest falls back to the
    default crest — the mosaic always goes out complete.
    """
    tiles: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries or ():
        civ_key = str(entry[0]).strip()
        origin = str(entry[1] or "").strip()
        if not civ_key or civ_key in seen:
            continue
        seen.add(civ_key)
        tiles.append((civ_key, origin))
    if not tiles:
        return None
    rows = (len(tiles) + MOSAIC_COLS - 1) // MOSAIC_COLS
    canvas_w = MOSAIC_COLS * MOSAIC_TILE
    canvas_h = rows * MOSAIC_TILE
    canvas = bytearray(canvas_w * canvas_h * 4)
    for index, (civ_key, origin) in enumerate(tiles):
        col, row = index % MOSAIC_COLS, index // MOSAIC_COLS
        tile_x, tile_y = col * MOSAIC_TILE, row * MOSAIC_TILE
        data = _DEFAULT_CREST_BYTES
        url = civ_crest_url(civ_key)
        if url is not None:
            downloaded = await _crest_bytes(url)
            if downloaded is not None:
                data = downloaded
        decoded = _decode_png_rgba(data) or _decode_png_rgba(_DEFAULT_CREST_BYTES)
        if decoded is None:
            continue
        crest_w, crest_h, crest_pixels = decoded
        crest = _resample_rgba(crest_w, crest_h, crest_pixels, MOSAIC_CREST, MOSAIC_CREST)
        _paste(
            canvas,
            canvas_w,
            tile_x + MOSAIC_TILE_PAD,
            tile_y + MOSAIC_TILE_PAD,
            MOSAIC_CREST,
            MOSAIC_CREST,
            crest,
        )
        code = ORIGIN_EMOJI_CODES.get(origin, DEFAULT_EMOJI_CODE)
        if not _draw_emoji_badge(
            canvas, canvas_w, tile_x, tile_y, await _emoji_bytes(code)
        ):
            _draw_badge(canvas, canvas_w, tile_x, tile_y, origin)
    return discord.File(io.BytesIO(_encode_png_rgba(canvas_w, canvas_h, bytes(canvas))), filename=filename)
