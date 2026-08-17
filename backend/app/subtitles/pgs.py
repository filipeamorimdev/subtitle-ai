"""Parse Blu-ray PGS (`.sup`) streams into timed bitmap events.

PGS is a sequence of PG segments. A display set ends at an END segment.
A set with composition objects starts (or replaces) a subtitle; a set with
no objects ends the current subtitle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

PCS = 0x16
PDS = 0x14
ODS = 0x15
END = 0x80

EPOCH_START = 0x80

ODS_LAST = 0x40
ODS_FIRST = 0x80
ODS_FIRST_AND_LAST = 0xC0


@dataclass(frozen=True)
class PgsEvent:
    start_ms: int
    end_ms: int
    image: Image.Image


@dataclass
class _Palette:
    colors: list[tuple[int, int, int, int]] = field(
        default_factory=lambda: [(0, 0, 0, 0)] * 256
    )


@dataclass
class _Object:
    width: int
    height: int
    rle: bytearray = field(default_factory=bytearray)


@dataclass
class _Composition:
    object_id: int
    x: int
    y: int
    cropped: bool = False
    crop_x: int = 0
    crop_y: int = 0
    crop_w: int = 0
    crop_h: int = 0


def pts_to_ms(pts_90k: int) -> int:
    return int(round(pts_90k / 90.0))


def iter_pgs_events(data: bytes) -> list[PgsEvent]:
    """Return completed subtitle events (start, end, cropped RGBA image)."""
    palettes: dict[int, _Palette] = {}
    objects: dict[int, _Object] = {}
    pending_ods: dict[int, _Object] = {}
    open_start: int | None = None
    open_image: Image.Image | None = None
    events: list[PgsEvent] = []

    for pts_90k, segments in _iter_display_sets(data):
        pts_ms = pts_to_ms(pts_90k)
        pcs = next((s for s in segments if s[0] == PCS), None)
        if pcs is None:
            continue
        payload = pcs[1]
        if len(payload) < 11:
            continue
        state = payload[7]
        if state == EPOCH_START:
            palettes.clear()
            objects.clear()
            pending_ods.clear()

        for kind, body in segments:
            if kind == PDS:
                pid, palette = _parse_pds(body)
                if palette is not None:
                    palettes[pid] = palette
            elif kind == ODS:
                _ingest_ods(body, objects, pending_ods)

        palette_id = payload[9]
        compositions = _parse_compositions(payload)
        image = _compose_frame(compositions, objects, palettes.get(palette_id))
        if image is None:
            if open_start is not None and open_image is not None:
                events.append(_close_event(open_start, pts_ms, open_image))
                open_start, open_image = None, None
            continue

        if open_start is not None and open_image is not None:
            events.append(_close_event(open_start, pts_ms, open_image))
        open_start = pts_ms
        open_image = image

    if open_start is not None and open_image is not None:
        events.append(_close_event(open_start, open_start + 5000, open_image))
    return events


def _close_event(start_ms: int, end_ms: int, image: Image.Image) -> PgsEvent:
    if end_ms <= start_ms:
        end_ms = start_ms + 1000
    return PgsEvent(start_ms=start_ms, end_ms=end_ms, image=image)


def _iter_display_sets(data: bytes):
    offset = 0
    length = len(data)
    current: list[tuple[int, bytes]] = []
    pts = 0
    while offset + 13 <= length:
        if data[offset : offset + 2] != b"PG":
            offset += 1
            continue
        pts = int.from_bytes(data[offset + 2 : offset + 6], "big")
        kind = data[offset + 10]
        size = int.from_bytes(data[offset + 11 : offset + 13], "big")
        start = offset + 13
        end = start + size
        if end > length:
            break
        payload = data[start:end]
        offset = end
        current.append((kind, payload))
        if kind == END:
            yield pts, current
            current = []
    if current:
        yield pts, current


def _parse_pds(payload: bytes) -> tuple[int, _Palette | None]:
    if len(payload) < 2:
        return 0, None
    palette_id = payload[0]
    palette = _Palette()
    entries = payload[2:]
    for i in range(0, len(entries) - 4, 5):
        index = entries[i]
        y, cr, cb, alpha = entries[i + 1], entries[i + 2], entries[i + 3], entries[i + 4]
        palette.colors[index] = _ycbcr_to_rgba(y, cr, cb, alpha)
    return palette_id, palette


def _ycbcr_to_rgba(y: int, cr: int, cb: int, alpha: int) -> tuple[int, int, int, int]:
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    return (
        max(0, min(255, int(round(r)))),
        max(0, min(255, int(round(g)))),
        max(0, min(255, int(round(b)))),
        alpha,
    )


def _ingest_ods(payload: bytes, objects: dict[int, _Object], pending: dict[int, _Object]) -> None:
    if len(payload) < 4:
        return
    object_id = int.from_bytes(payload[0:2], "big")
    seq = payload[3]
    if seq in {ODS_FIRST, ODS_FIRST_AND_LAST}:
        if len(payload) < 11:
            return
        width = int.from_bytes(payload[7:9], "big")
        height = int.from_bytes(payload[9:11], "big")
        obj = _Object(width=width, height=height, rle=bytearray(payload[11:]))
        if seq == ODS_FIRST_AND_LAST:
            objects[object_id] = obj
            pending.pop(object_id, None)
        else:
            pending[object_id] = obj
        return
    if seq == ODS_LAST:
        obj = pending.pop(object_id, None)
        if obj is None:
            return
        obj.rle.extend(payload[4:])
        objects[object_id] = obj


def _parse_compositions(pcs_payload: bytes) -> list[_Composition]:
    count = pcs_payload[10]
    offset = 11
    compositions: list[_Composition] = []
    for _ in range(count):
        if offset + 8 > len(pcs_payload):
            break
        object_id = int.from_bytes(pcs_payload[offset : offset + 2], "big")
        flags = pcs_payload[offset + 3]
        x = int.from_bytes(pcs_payload[offset + 4 : offset + 6], "big")
        y = int.from_bytes(pcs_payload[offset + 6 : offset + 8], "big")
        cropped = bool(flags & 0x80)
        if cropped:
            if offset + 16 > len(pcs_payload):
                break
            compositions.append(
                _Composition(
                    object_id=object_id,
                    x=x,
                    y=y,
                    cropped=True,
                    crop_x=int.from_bytes(pcs_payload[offset + 8 : offset + 10], "big"),
                    crop_y=int.from_bytes(pcs_payload[offset + 10 : offset + 12], "big"),
                    crop_w=int.from_bytes(pcs_payload[offset + 12 : offset + 14], "big"),
                    crop_h=int.from_bytes(pcs_payload[offset + 14 : offset + 16], "big"),
                )
            )
            offset += 16
        else:
            compositions.append(_Composition(object_id=object_id, x=x, y=y))
            offset += 8
    return compositions


def decode_rle(data: bytes, width: int, height: int) -> bytes:
    """Decode PGS object RLE into palette-index bytes (row-major)."""
    if width <= 0 or height <= 0:
        return b""
    pixels = bytearray(width * height)
    i = 0
    x = 0
    y = 0
    length = len(data)

    def _put(count: int, color: int) -> None:
        nonlocal x
        if y >= height or count <= 0:
            return
        room = width - x
        if room <= 0:
            return
        n = min(count, room)
        start = y * width + x
        pixels[start : start + n] = bytes([color]) * n
        x += n

    while y < height and i < length:
        first = data[i]
        i += 1
        if first:
            _put(1, first)
            continue
        if i >= length:
            break
        second = data[i]
        i += 1
        if second == 0:
            x = 0
            y += 1
            continue
        if second < 64:
            _put(second, 0)
            continue
        if i >= length:
            break
        third = data[i]
        i += 1
        if second < 128:
            _put(((second - 64) << 8) + third, 0)
        elif second < 192:
            _put(second - 128, third)
        else:
            if i >= length:
                break
            fourth = data[i]
            i += 1
            _put(((second - 192) << 8) + third, fourth)
    return bytes(pixels)


def _compose_frame(
    compositions: list[_Composition],
    objects: dict[int, _Object],
    palette: _Palette | None,
) -> Image.Image | None:
    if not compositions:
        return None
    colors = (palette or _Palette()).colors
    parts: list[tuple[int, int, Image.Image]] = []
    max_x = 0
    max_y = 0
    for comp in compositions:
        obj = objects.get(comp.object_id)
        if obj is None or obj.width <= 0 or obj.height <= 0:
            continue
        indexes = decode_rle(bytes(obj.rle), obj.width, obj.height)
        rgba = bytearray(obj.width * obj.height * 4)
        for idx, color_index in enumerate(indexes):
            r, g, b, a = colors[color_index]
            base = idx * 4
            rgba[base : base + 4] = (r, g, b, a)
        tile = Image.frombytes("RGBA", (obj.width, obj.height), bytes(rgba))
        if comp.cropped and comp.crop_w > 0 and comp.crop_h > 0:
            box = (
                comp.crop_x,
                comp.crop_y,
                min(obj.width, comp.crop_x + comp.crop_w),
                min(obj.height, comp.crop_y + comp.crop_h),
            )
            tile = tile.crop(box)
        parts.append((comp.x, comp.y, tile))
        max_x = max(max_x, comp.x + tile.size[0])
        max_y = max(max_y, comp.y + tile.size[1])
    if not parts:
        return None
    canvas = Image.new("RGBA", (max(1, max_x), max(1, max_y)), (0, 0, 0, 0))
    for x, y, tile in parts:
        canvas.paste(tile, (x, y), tile)
    return _crop_content(canvas)


def _crop_content(image: Image.Image) -> Image.Image | None:
    alpha = image.split()[-1]
    bbox = alpha.getbbox()
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    pad = 8
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(image.width, right + pad)
    bottom = min(image.height, bottom + pad)
    return image.crop((left, top, right, bottom))
