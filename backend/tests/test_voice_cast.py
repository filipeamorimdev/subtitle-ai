"""Unit tests for the editable AI voice-cast proposal parser."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.localization_routes import _voice_cast_out
from app.db import Base
from app.db.models import MediaItemRow
from app.localization.dubbing.models import SpeechSegment
from app.localization.dubbing import voice_cast
from app.localization.dubbing.voice_cast import (
    VoiceCastDraftService,
    VoiceCastResult,
    VoiceCastSuggestion,
    _select_sample_cues,
    _suggestions_from_response,
)


def test_voice_cast_parser_keeps_only_sampled_cues_and_fills_gaps():
    suggestions = _suggestions_from_response(
        '''{
          "speakers": [
            {"speaker_id": "Ryder", "voice_style": "warm adult", "cue_indices": [3, 8], "confidence": 0.81},
            {"speaker_id": "Boo", "voice_style": "young, bright", "cue_indices": [14, 99]}
          ]
        }''',
        allowed_cues={3, 8, 14, 21},
        default_voice_model="chatterbox-multilingual-v3:pt-PT:natural",
    )

    assert suggestions[0].speaker_id == "Ryder"
    assert suggestions[0].cue_indices == [3, 8]
    assert suggestions[0].confidence == 0.81
    assert suggestions[1].cue_indices == [14]
    assert suggestions[-1].speaker_id == "Unidentified speaker"
    assert suggestions[-1].cue_indices == [21]
    assert suggestions[0].voice_model.endswith(":expressive")
    assert suggestions[1].voice_model.endswith(":expressive")
    assert suggestions[-1].voice_model.endswith(":natural")


def test_voice_cast_sampling_is_bounded_and_evenly_spread():
    segments = [
        SpeechSegment(start=float(index * 10), end=float(index * 10 + 4), text="Dialogue", source_cues=[index])
        for index in range(1, 31)
    ]

    samples = _select_sample_cues(segments)

    assert len(samples) == 14
    assert samples[0].index == 1
    assert samples[-1].index == 30
    assert sum(sample.duration for sample in samples) <= 56.0


@pytest.mark.asyncio
async def test_voice_cast_combines_normalized_clips_with_ffmpeg(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    async def fake_run_process(command, **kwargs):
        commands.append(list(command))
        output = Path(kwargs["output_paths"][0])
        if "concat=n=" in " ".join(command):
            output.write_bytes(b"combined audio")
        else:
            output.write_bytes(b"x" * 45)

    monkeypatch.setattr(voice_cast, "run_process_checked", fake_run_process)
    sample = await voice_cast._build_audio_sample(
        tmp_path / "episode.mkv",
        [
            voice_cast._SampledCue(index=3, start=1.0, duration=2.0, text="One"),
            voice_cast._SampledCue(index=9, start=7.0, duration=2.5, text="Two"),
        ],
    )

    assert sample == b"combined audio"
    assert len(commands) == 3
    assert commands[-1][0] == "ffmpeg"
    assert "[0:a][1:a]concat=n=2:v=0:a=1[combined]" in commands[-1]


def test_voice_cast_draft_is_persisted_and_uses_only_enabled_assignments(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'voice-cast.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    media = MediaItemRow(
        provider_id="bazarr",
        external_id="episode:99",
        media_type="episode",
        title="Big Jon",
    )
    db.add(media)
    db.commit()
    db.refresh(media)

    service = VoiceCastDraftService(db)
    draft = service.save_analysis(
        media,
        target_language="pt-PT",
        mix_mode="voiceover_preview",
        result=VoiceCastResult(
            provider_id="openrouter",
            model_id="nvidia/test-audio",
            analysed_cue_count=3,
            metadata_used={"season": 1},
            suggestions=[
                VoiceCastSuggestion(
                    "Speaker 1", "Warm", [3, 9], 0.8,
                    "chatterbox-multilingual-v3:pt-PT:expressive",
                ),
                VoiceCastSuggestion(
                    "Speaker 2", "Bright", [15], 0.6,
                    "chatterbox-multilingual-v3:pt-PT:expressive",
                ),
            ],
        ),
    )

    assert draft.id is not None
    assert draft.mix_mode == "voiceover_preview"
    assert service.get(media.id, "pt-PT").id == draft.id

    saved = service.update(
        draft,
        mix_mode="background_preserved",
        suggestions=[
            {
                "speaker_id": "Narrator",
                "voice_style": "Calm",
                "cue_indices": [3, 9],
                "confidence": 0.75,
                "voice_model": "chatterbox-multilingual-v3:pt-PT:calm",
                "enabled": True,
            },
            {
                "speaker_id": "Speaker 2",
                "voice_style": "Bright",
                "cue_indices": [15],
                "confidence": 0.6,
                "voice_model": "chatterbox-multilingual-v3:pt-PT:expressive",
                "enabled": False,
            },
        ],
    )

    assert saved.mix_mode == "background_preserved"
    assert service.speaker_voice_overrides(saved) == {
        "cue:3": "chatterbox-multilingual-v3:pt-PT:calm",
        "cue:9": "chatterbox-multilingual-v3:pt-PT:calm",
    }
    public_draft = _voice_cast_out(saved)
    assert public_draft.id == draft.id
    assert public_draft.suggestions[0].speaker_id == "Narrator"
    assert any(item.id.endswith(":natural") for item in public_draft.available_voice_models)
