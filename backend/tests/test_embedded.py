"""Embedded subtitle probe/extract helpers."""

from __future__ import annotations

from app.subtitles.embedded import (
    EmbeddedTrack,
    classify_codec,
    pick_extractable_track,
)
from app.subtitles.filenames import build_external_subtitle_path


def test_classify_codec():
    assert classify_codec("subrip") == ("text", True)
    assert classify_codec("mov_text") == ("text", True)
    assert classify_codec("hdmv_pgs_subtitle") == ("image", False)
    assert classify_codec("dvd_subtitle") == ("image", False)
    assert classify_codec(None) == ("unknown", False)


def test_pick_extractable_track_prefers_matching_language():
    tracks = [
        EmbeddedTrack(1, "fr", "subrip", "text", True),
        EmbeddedTrack(2, "en", "subrip", "text", True, hi=True),
        EmbeddedTrack(3, "en", "hdmv_pgs_subtitle", "image", False),
    ]
    picked = pick_extractable_track(tracks, ["en"])
    assert picked is not None
    assert picked.stream_index == 2
    assert picked.extractable is True


def test_build_external_subtitle_path(tmp_path):
    media = tmp_path / "Show - S01E01.mkv"
    assert build_external_subtitle_path(media, "en") == tmp_path / "Show - S01E01.en.srt"
