"""Embedded subtitle probe/extract helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.subtitles.embedded import (
    EmbeddedTrack,
    classify_codec,
    extract_text_track,
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


@pytest.mark.asyncio
async def test_extract_text_track_handles_plus_in_filename(tmp_path, monkeypatch):
    media = tmp_path / "Show - S01E01 + S01E02.mkv"
    media.write_bytes(b"fake")
    output = tmp_path / "Show - S01E01 + S01E02.en.srt"
    seen: dict[str, list[str]] = {}

    async def fake_exec(*command, **kwargs):
        seen["command"] = [str(c) for c in command]

        class Proc:
            returncode = 0

            async def communicate(self):
                # ffmpeg writes to the last arg
                out = Path(command[-1])
                out.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
                return b"", b""

        return Proc()

    monkeypatch.setattr("app.subtitles.embedded.asyncio.create_subprocess_exec", fake_exec)
    monkeypatch.setattr("app.subtitles.embedded.shutil.which", lambda _: "/usr/bin/ffmpeg")

    result = await extract_text_track(media, 2, output)
    assert result == output
    assert output.exists()
    assert "-f" in seen["command"]
    assert "srt" in seen["command"]
    # Must not write a .srt.partial next to media (ffmpeg muxer fails on that)
    assert not any(str(p).endswith(".partial") for p in tmp_path.iterdir())
    assert seen["command"][-1].endswith("extract.srt")
