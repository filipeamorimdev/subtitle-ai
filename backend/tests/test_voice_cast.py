"""Unit tests for the editable AI voice-cast proposal parser."""

from app.localization.dubbing.models import SpeechSegment
from app.localization.dubbing.voice_cast import _select_sample_cues, _suggestions_from_response


def test_voice_cast_parser_keeps_only_sampled_cues_and_fills_gaps():
    suggestions = _suggestions_from_response(
        '''{
          "speakers": [
            {"speaker_id": "Ryder", "voice_style": "warm adult", "cue_indices": [3, 8], "confidence": 0.81},
            {"speaker_id": "Boo", "voice_style": "young, bright", "cue_indices": [14, 99]}
          ]
        }''',
        allowed_cues={3, 8, 14, 21},
        default_voice_model="pt_PT-tugão-medium",
    )

    assert suggestions[0].speaker_id == "Ryder"
    assert suggestions[0].cue_indices == [3, 8]
    assert suggestions[0].confidence == 0.81
    assert suggestions[1].cue_indices == [14]
    assert suggestions[-1].speaker_id == "Unidentified speaker"
    assert suggestions[-1].cue_indices == [21]
    assert all(item.voice_model == "pt_PT-tugão-medium" for item in suggestions)


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
