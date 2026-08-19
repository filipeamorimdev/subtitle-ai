"""Reading-speed and glossary helpers."""

from __future__ import annotations

from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.reading import analyze_document, parse_srt_timestamp
from app.translation.prompts import build_system_prompt


def test_cps_flags_overcrowded_cue():
    document = SubtitleDocument(
        format="srt",
        encoding="utf-8",
        blocks=[
            SubtitleBlock(
                index=1,
                start="00:00:00,000",
                end="00:00:00,400",
                text="This line is far too long to read comfortably in the available time window on screen",
            )
        ],
    )
    issues = analyze_document(document)
    assert any(item.kind == "cps_too_high" for item in issues)


def test_timestamp_parser():
    assert parse_srt_timestamp("00:01:02,500") == 62500


def test_locale_note_in_prompt():
    prompt = build_system_prompt(
        "fr-CA",
        "French (Canada)",
        locale_note="Use Canadian French.",
        glossary_block="- Neo → Néo",
    )
    assert "Canadian French" in prompt
    assert "Neo → Néo" in prompt
