"""SourceResolver scoring tests."""

from __future__ import annotations

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


def test_french_subtitle_does_not_block_english_transcription():
    resolution = resolve_from_candidates(
        [
            SourceCandidate(type=SourceType.SUBTITLE, language="fr", path="/m/Movie.fr.srt"),
            SourceCandidate(type=SourceType.TRANSCRIPT, language="en"),
        ],
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.TRANSCRIPT
    payload = resolution.to_dict()
    assert payload["selected"] == "transcript"
    assert payload["score"] is not None


def test_french_embedded_text_does_not_beat_transcription():
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
    assert resolution.selected.type == SourceType.TRANSCRIPT


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
