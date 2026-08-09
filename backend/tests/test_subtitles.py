"""Subtitle parser and validation tests."""

from __future__ import annotations

import pytest

from app.subtitles.filenames import build_target_subtitle_path, detect_language_from_filename
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
    assert str(build_target_subtitle_path("movie.en.srt", "pt-PT")) == "movie.pt-PT.srt"
    assert str(build_target_subtitle_path("movie.en.hi.srt", "pt-PT")) == "movie.pt-PT.srt"
    assert str(build_target_subtitle_path("movie.srt", "pt-PT")) == "movie.pt-PT.srt"
    assert str(build_target_subtitle_path("/media/x/movie.eng.srt", "pt-PT")).endswith("movie.pt-PT.srt")


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

def test_atomic_write(tmp_path):
    doc = parse_srt(SAMPLE)
    target = tmp_path / "movie.pt-PT.srt"
    write_srt_atomic(target, doc)
    assert target.exists()
    with pytest.raises(FileExistsError):
        write_srt_atomic(target, doc)
    assert validate_source(doc).ok
