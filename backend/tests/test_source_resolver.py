"""SourceResolver scoring tests."""

from __future__ import annotations

import pytest

from app.localization.source_resolver import (
    SourceCandidate,
    SourceType,
    resolve_from_candidates,
)
from app.localization.transcription.audio_selector import (
    AudioStream,
    select_audio_stream,
)


def test_preferred_english_subtitle_beats_transcription():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.SUBTITLE, language="en", path="/m/Movie.en.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.SUBTITLE
    assert resolution.selected.language == "en"
    assert "Preferred" in resolution.reason


@pytest.mark.asyncio
async def test_english_dialogue_in_hi_sidecar_beats_transcription(tmp_path):
    from app.localization.source_resolver import SourceResolver

    media = tmp_path / "Show - S01E01.mkv"
    media.write_bytes(b"x")
    sidecar = tmp_path / "Show - S01E01.hi.srt"
    sidecar.write_text(
        "1\n00:00:01,000 --> 00:00:03,000\nWe'll be there on the double.\n\n"
        "2\n00:00:04,000 --> 00:00:06,000\nWhenever there's a problem.\n\n"
        "3\n00:00:07,000 --> 00:00:09,000\nWhy does she not go in?\nShe is hot and thirsty.\n\n"
        "4\n00:00:10,000 --> 00:00:12,000\nShe's missing her family.\n\n"
        "5\n00:00:13,000 --> 00:00:15,000\nMarshall, keep her busy until we find them.\n\n"
        "6\n00:00:16,000 --> 00:00:18,000\nNo problem! They are on the way.\n",
        encoding="utf-8",
    )

    resolution = await SourceResolver().resolve(
        media,
        preferred_languages=["en"],
        target_language="pt-PT",
        embedded_tracks=[],
        include_transcript=True,
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.SUBTITLE
    assert resolution.selected.language == "en"
    assert resolution.selected.path == str(sidecar)


def test_other_language_subtitle_beats_transcription():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.SUBTITLE, language="fr", path="/m/Movie.fr.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.SUBTITLE
    assert resolution.selected.language == "fr"


def test_hindi_subtitle_beats_transcription():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.SUBTITLE, language="hi", path="/m/Movie.hi.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.SUBTITLE
    assert resolution.selected.language == "hi"


def test_preferred_english_sidecar_still_beats_other_language_sidecar():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.SUBTITLE, language="hi", path="/m/Movie.hi.srt"),
            SourceCandidate(type=SourceType.SUBTITLE, language="en", path="/m/Movie.en.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.SUBTITLE
    assert resolution.selected.language == "en"


def test_other_language_embedded_text_beats_transcription():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(
                type=SourceType.EMBEDDED_SUBTITLE,
                language="fr",
                stream_index=3,
                extractable=True,
            ),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.EMBEDDED_SUBTITLE
    assert resolution.selected.language == "fr"


def test_target_subtitle_wins():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.TARGET_SUBTITLE, language="pt-PT", path="/m/Movie.pt.srt"),
            SourceCandidate(type=SourceType.SUBTITLE, language="en", path="/m/Movie.en.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.TARGET_SUBTITLE


def test_english_default_audio_selected_over_commentary_and_portuguese():
    selection = select_audio_stream(
        [
            AudioStream(stream_index=1, language="en", channels=6, default=True, title="English 5.1"),
            AudioStream(
                stream_index=2,
                language="en",
                channels=2,
                comment=True,
                title="English commentary",
            ),
            AudioStream(stream_index=3, language="pt", channels=2, title="Portuguese"),
        ],
        preferred_languages=["en"],
    )
    assert selection.selected is not None
    assert selection.selected.stream.stream_index == 1
    payload = selection.to_dict()
    assert payload["stream_index"] == 1
    assert payload["language"] == "en"
    assert "Commentary" in " ".join(selection.selected.reasons) or "Non-commentary" in payload["reason"]


def test_audio_description_is_penalized():
    selection = select_audio_stream(
        [
            AudioStream(stream_index=0, language="en", channels=2, title="Audio Description"),
            AudioStream(stream_index=1, language="en", channels=2, default=True, title="English"),
        ],
        preferred_languages=["en"],
    )
    assert selection.selected is not None
    assert selection.selected.stream.stream_index == 1
