"""PGS parser tests."""

from __future__ import annotations

from app.subtitles.pgs import decode_rle, iter_pgs_events, pts_to_ms


def test_decode_rle_solid_rows():
    # Two rows of four color-1 pixels.
    rle = bytes.fromhex("00 84 01 00 00 00 84 01 00 00")
    pixels = decode_rle(rle, 4, 2)
    assert pixels == bytes([1] * 8)


def test_pts_to_ms():
    assert pts_to_ms(0) == 0
    assert pts_to_ms(90_000) == 1000


def _segment(kind: int, pts: int, payload: bytes) -> bytes:
    return (
        b"PG"
        + pts.to_bytes(4, "big")
        + (0).to_bytes(4, "big")
        + bytes([kind])
        + len(payload).to_bytes(2, "big")
        + payload
    )


def _minimal_sup() -> bytes:
    rle = bytes.fromhex("00 84 01 00 00 00 84 01 00 00")
    data_len = (4 + len(rle)).to_bytes(3, "big")
    ods = (
        (1).to_bytes(2, "big")  # object id
        + bytes([0, 0xC0])  # version, first+last
        + data_len
        + (4).to_bytes(2, "big")
        + (2).to_bytes(2, "big")
        + rle
    )
    pds = bytes([0, 0, 1, 255, 128, 128, 255])
    pcs_start = (
        (8).to_bytes(2, "big")
        + (8).to_bytes(2, "big")
        + bytes([16])
        + (0).to_bytes(2, "big")
        + bytes([0x80, 0, 0, 1])  # epoch start, palette 0, 1 object
        + (1).to_bytes(2, "big")
        + bytes([0, 0])
        + (0).to_bytes(2, "big")
        + (0).to_bytes(2, "big")
    )
    pcs_end = (
        (8).to_bytes(2, "big")
        + (8).to_bytes(2, "big")
        + bytes([16])
        + (1).to_bytes(2, "big")
        + bytes([0x00, 0, 0, 0])
    )
    start_pts = 90_000  # 1s
    end_pts = 270_000  # 3s
    return b"".join(
        [
            _segment(0x16, start_pts, pcs_start),
            _segment(0x14, start_pts, pds),
            _segment(0x15, start_pts, ods),
            _segment(0x80, start_pts, b""),
            _segment(0x16, end_pts, pcs_end),
            _segment(0x80, end_pts, b""),
        ]
    )


def test_iter_pgs_events_times_and_image():
    events = iter_pgs_events(_minimal_sup())
    assert len(events) == 1
    event = events[0]
    assert event.start_ms == 1000
    assert event.end_ms == 3000
    assert event.image.mode == "RGBA"
    assert event.image.size[0] >= 4
    assert event.image.size[1] >= 2
    # Object pixels are opaque white after YCbCr conversion.
    extrema = event.image.getextrema()
    assert extrema[3][1] == 255
