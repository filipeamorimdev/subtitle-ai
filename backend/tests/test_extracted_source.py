"""Extracted-source sidecar cleanup helpers."""

from __future__ import annotations

from app.subtitles.filenames import unlink_extracted_source


def test_unlink_extracted_source_deletes_sidecar(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_text("x")
    source = tmp_path / "Movie.en.srt"
    source.write_text("1\n")
    target = tmp_path / "Movie.pt.srt"
    target.write_text("1\n")

    assert unlink_extracted_source(
        source,
        target_path=target,
        media_path=media,
        media_roots=[str(tmp_path)],
    )
    assert not source.exists()
    assert target.exists()
    assert media.exists()


def test_unlink_extracted_source_skips_target_and_media(tmp_path):
    media = tmp_path / "Movie.mkv"
    media.write_text("x")
    target = tmp_path / "Movie.pt.srt"
    target.write_text("1\n")

    assert not unlink_extracted_source(target, target_path=target, media_path=media)
    assert target.exists()
    assert not unlink_extracted_source(media, media_path=media)
    assert media.exists()


def test_unlink_extracted_source_skips_outside_roots(tmp_path):
    source = tmp_path / "Movie.en.srt"
    source.write_text("1\n")
    other = tmp_path / "other"
    other.mkdir()
    assert not unlink_extracted_source(source, media_roots=[str(other)])
    assert source.exists()


def test_unlink_extracted_source_missing_file(tmp_path):
    missing = tmp_path / "Movie.en.srt"
    assert not unlink_extracted_source(missing)
