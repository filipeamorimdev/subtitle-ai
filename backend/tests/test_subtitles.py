"""Subtitle parser and validation tests."""

from __future__ import annotations

import pytest

from app.subtitles.filenames import (
    build_target_subtitle_path,
    detect_language_from_filename,
    language_chip_available,
    suppress_generic_language_chip,
)
from app.subtitles.markup import protect_markup, restore_markup
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.parsers.srt import SrtParseError, parse_srt
from app.subtitles.validation import validate_batch_mapping, validate_source, validate_translation
from app.subtitles.writer.srt import render_srt, write_srt_atomic


SAMPLE = """1
00:00:01,000 --> 00:00:03,000
Hello, how are you?

2
00:00:04,000 --> 00:00:06,000
I'm fine, thank you.
"""


def test_parse_valid_srt():
    doc = parse_srt(SAMPLE)
    assert len(doc.blocks) == 2
    assert doc.blocks[0].text == "Hello, how are you?"
    assert doc.blocks[1].start == "00:00:04,000"


def test_parse_multiline_and_tags():
    content = """1
00:00:01,000 --> 00:00:03,000
<i>Olá</i>
segunda linha

2
00:00:04,000 --> 00:00:06,000
<b>Obrigado</b>
"""
    doc = parse_srt(content)
    assert "<i>Olá</i>" in doc.blocks[0].text
    assert "segunda linha" in doc.blocks[0].text


def test_parse_malformed_raises():
    with pytest.raises(SrtParseError):
        parse_srt("not an srt")


def test_unicode_portuguese():
    content = """1
00:00:01,000 --> 00:00:02,000
Não, estás aí? — sim!
"""
    doc = parse_srt(content)
    assert "Não" in doc.blocks[0].text


def test_roundtrip_render():
    doc = parse_srt(SAMPLE)
    rendered = render_srt(doc)
    again = parse_srt(rendered)
    assert [b.text for b in again.blocks] == [b.text for b in doc.blocks]


def test_markup_protect_restore():
    original = "<i>Hello</i> <b>world</b>"
    protected = protect_markup(original)
    assert "<TAG0>" in protected.protected_text
    restored = restore_markup(protected.protected_text.replace("Hello", "Olá").replace("world", "mundo"), protected.tags)
    assert restored == "<i>Olá</i> <b>mundo</b>"


def test_validate_translation_detects_issues():
    source = parse_srt(SAMPLE)
    bad = SubtitleDocument(
        format="srt",
        encoding="utf-8",
        blocks=[
            SubtitleBlock(index=1, start="00:00:01,000", end="00:00:03,000", text=""),
            SubtitleBlock(index=9, start="00:00:99,000", end="00:00:06,000", text="x"),
        ],
    )
    result = validate_translation(source, bad)
    assert not result.ok
    codes = {i.code for i in result.issues}
    assert "empty_translation" in codes
    assert "block_id" in codes
    assert "timestamp" in codes


def test_validate_markup_mismatch():
    source = parse_srt("""1
00:00:01,000 --> 00:00:02,000
<i>Hello</i>
""")
    translated = SubtitleDocument(
        format="srt",
        encoding="utf-8",
        blocks=[
            SubtitleBlock(index=1, start="00:00:01,000", end="00:00:02,000", text="Olá"),
        ],
    )
    result = validate_translation(source, translated)
    assert not result.ok
    assert result.hard_ok
    assert any(i.code == "markup" for i in result.soft_issues)


def test_validate_batch_mapping():
    result = validate_batch_mapping([1, 2], {1: "a"})
    assert not result.ok
    assert any(i.code == "missing_blocks" for i in result.issues)


def test_filenames():
    assert detect_language_from_filename("movie.en.srt") == "en"
    assert detect_language_from_filename("movie.eng.srt") == "en"
    assert detect_language_from_filename("movie.en-US.srt") == "en"
    assert detect_language_from_filename("movie.en.hi.srt") == "en"
    assert detect_language_from_filename("movie.en.sdh.srt") == "en"
    assert str(build_target_subtitle_path("movie.en.srt", "pt-PT")) == "movie.pt.srt"
    assert str(build_target_subtitle_path("movie.en.hi.srt", "pt-PT")) == "movie.pt.srt"
    assert str(build_target_subtitle_path("movie.srt", "pt-PT")) == "movie.pt.srt"
    assert str(build_target_subtitle_path("/media/x/movie.eng.srt", "pt-PT")).endswith("movie.pt.srt")
    # Never keep the source language in the target name (stacked tags / bad legacy names)
    assert (
        str(build_target_subtitle_path("movie.en.pt-PT.srt", "pt-PT")) == "movie.pt.srt"
    )
    assert str(
        build_target_subtitle_path(
            "Star Wars - Young Jedi Adventures - S02E02 - A Jedi or a Pirate WEBDL-1080p.en.srt",
            "pt-PT",
        )
    ) == (
        "Star Wars - Young Jedi Adventures - S02E02 - A Jedi or a Pirate WEBDL-1080p.pt.srt"
    )
    # Prefer media stem when a video path is provided
    assert str(
        build_target_subtitle_path(
            "/media/x/movie.en.srt",
            "pt-PT",
            media_path="/media/x/movie.mkv",
        )
    ) == "/media/x/movie.pt.srt"
    assert str(build_target_subtitle_path("movie.en.srt", "pt-BR")) == "movie.pt-BR.srt"


def test_language_chip_available_does_not_cross_portuguese_locales():
    present_pt = {"pt"}
    assert language_chip_available("pt", present_pt) is True
    assert language_chip_available("pt-PT", present_pt) is True
    assert language_chip_available("pt-BR", present_pt) is False
    present_br = {"pt-BR"}
    assert language_chip_available("pt", present_br) is False
    assert language_chip_available("pt-PT", present_br) is False
    assert language_chip_available("pt-BR", present_br) is True
    assert language_chip_available("pt-PT", set()) is False


def test_suppress_generic_portuguese_chip_when_portugal_represents_sidecar():
    present = {"pt"}
    featured = ["en", "pt", "pt-PT", "pt-BR"]
    assert suppress_generic_language_chip("pt", present, featured) is True
    assert suppress_generic_language_chip("pt-PT", present, featured) is False
    assert suppress_generic_language_chip("pt-BR", present, featured) is False
    assert suppress_generic_language_chip("en", {"en", "pt"}, featured) is False


def test_ensure_canonical_sidecar_collapses_pt_pt_duplicate(tmp_path):
    from app.subtitles.filenames import ensure_canonical_sidecar

    ietf = tmp_path / "Futurama - S07E16 - T. - The Terrestrial Bluray-1080p.pt-PT.srt"
    canonical = tmp_path / "Futurama - S07E16 - T. - The Terrestrial Bluray-1080p.pt.srt"
    ietf.write_text("1\n00:00:01,000 --> 00:00:02,000\nOlá\n", encoding="utf-8")
    out = ensure_canonical_sidecar(ietf, "pt-PT")
    assert out == canonical
    assert canonical.is_file()
    assert not ietf.exists()

    canonical.write_text("canonical\n", encoding="utf-8")
    ietf.write_text("duplicate\n", encoding="utf-8")
    again = ensure_canonical_sidecar(ietf, "pt-PT")
    assert again == canonical
    assert not ietf.exists()
    assert canonical.read_text(encoding="utf-8") == "canonical\n"


def test_find_source_accepts_hi(tmp_path):
    from app.subtitles.filenames import find_source_srt_beside_media

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    hi = tmp_path / "Show - S01E01.en.hi.srt"
    hi.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi\n", encoding="utf-8")
    found = find_source_srt_beside_media(media, ["en"])
    assert found is not None
    assert found[0] == hi
    assert found[1] == "en"


def test_find_source_does_not_borrow_sibling_episode(tmp_path):
    """Season folders often share many *.en.srt files; only match this media's stem."""
    from app.subtitles.filenames import find_source_srt_beside_media

    other_en = tmp_path / (
        "Dinosaur Train - S01E01-E02 - Valley of the Stygimolochs.en.srt"
    )
    other_pt = tmp_path / (
        "Dinosaur Train - S01E01-E02 - Valley of the Stygimolochs.pt-PT.srt"
    )
    other_en.write_text("en\n", encoding="utf-8")
    other_pt.write_text("pt\n", encoding="utf-8")

    media = tmp_path / (
        "Dinosaur Train - S01E03-E04 - The Call of the Wild Corythosaurus.mkv"
    )
    media.write_bytes(b"x")

    assert find_source_srt_beside_media(media, ["en"]) is None

    own = tmp_path / (
        "Dinosaur Train - S01E03-E04 - The Call of the Wild Corythosaurus.en.srt"
    )
    own.write_text("en\n", encoding="utf-8")
    found = find_source_srt_beside_media(media, ["en"])
    assert found is not None
    assert found[0] == own


def test_find_source_accepts_other_language_when_preferred_missing(tmp_path):
    from app.subtitles.filenames import find_source_srt_beside_media

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    fr = tmp_path / "Show - S01E01.fr.srt"
    fr.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n", encoding="utf-8")
    found = find_source_srt_beside_media(media, ["en"], target_language="pt-PT")
    assert found is not None
    assert found[0] == fr
    assert found[1] == "fr"


def test_find_source_prefers_configured_language_over_other(tmp_path):
    from app.subtitles.filenames import find_source_srt_beside_media

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    fr = tmp_path / "Show - S01E01.fr.srt"
    en = tmp_path / "Show - S01E01.en.srt"
    fr.write_text("fr\n", encoding="utf-8")
    en.write_text("en\n", encoding="utf-8")
    found = find_source_srt_beside_media(media, ["en"], target_language="pt-PT")
    assert found is not None
    assert found[0] == en
    assert found[1] == "en"


def test_find_source_skips_target_language_sidecar(tmp_path):
    from app.subtitles.filenames import find_source_srt_beside_media

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    pt = tmp_path / "Show - S01E01.pt.srt"
    pt.write_text("pt\n", encoding="utf-8")
    assert find_source_srt_beside_media(media, ["en"], target_language="pt-PT") is None

    fr = tmp_path / "Show - S01E01.fr.srt"
    fr.write_text("fr\n", encoding="utf-8")
    found = find_source_srt_beside_media(media, ["en"], target_language="pt-PT")
    assert found is not None
    assert found[0] == fr


def test_find_source_strict_preferred_ignores_other_languages(tmp_path):
    from app.subtitles.filenames import find_source_srt_beside_media

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    fr = tmp_path / "Show - S01E01.fr.srt"
    fr.write_text("fr\n", encoding="utf-8")
    assert (
        find_source_srt_beside_media(
            media, ["en"], allow_other_languages=False
        )
        is None
    )


def test_subtitle_belongs_to_media_rejects_prefix_collisions(tmp_path):
    from app.subtitles.filenames import subtitle_belongs_to_media

    media = tmp_path / "Show - S01E01.mkv"
    dual = tmp_path / "Show - S01E01-E02.en.srt"
    dual.write_text("x", encoding="utf-8")
    assert not subtitle_belongs_to_media(dual, media)

    exact = tmp_path / "Show - S01E01.en.hi.srt"
    exact.write_text("x", encoding="utf-8")
    assert subtitle_belongs_to_media(exact, media)

def test_atomic_write(tmp_path):
    doc = parse_srt(SAMPLE)
    target = tmp_path / "movie.pt-PT.srt"
    write_srt_atomic(target, doc)
    assert target.exists()
    with pytest.raises(FileExistsError):
        write_srt_atomic(target, doc)
    assert validate_source(doc).ok
