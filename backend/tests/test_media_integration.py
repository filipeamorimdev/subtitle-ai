"""Optional end-to-end media smoke test.

Run with::

    pytest -m media_integration
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.jobs.event_log import JobEventLog
from app.localization.dubbing.pipeline import DubbingPipeline, probe_media_artifact
from app.localization.dubbing.options import DUB_MIX_VOICEOVER_PREVIEW
from app.localization.source_resolver import SourceResolver, SourceType
from app.localization.transcription.audio_selector import AudioTrackSelector
from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord
from app.localization.transcription.service import TranscriptionService, extract_audio
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.writer.srt import write_srt_atomic
from tests.fixtures.media import build_multitrack_mkv, ffmpeg_available


pytestmark = [
    pytest.mark.media_integration,
    pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not installed"),
]


class _FakeASR:
    name = "fake"

    async def transcribe(self, audio_path, language=None, word_timestamps=True, **kwargs):
        return Transcript(
            language="en",
            language_confidence=0.97,
            requested_language=language,
            provider="fake",
            duration=2.0,
            segments=(
                TranscriptSegment(
                    start=0.3,
                    end=1.6,
                    text="Hello from English audio",
                    words=(
                        TranscriptWord(0.3, 0.6, "Hello"),
                        TranscriptWord(0.6, 0.9, "from"),
                        TranscriptWord(0.9, 1.2, "English"),
                        TranscriptWord(1.2, 1.6, "audio"),
                    ),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_smoke_select_transcribe_format_dub_mux(tmp_path, monkeypatch):
    mkv = build_multitrack_mkv(tmp_path / "Movie.mkv", tmp_path / "parts")
    (tmp_path / "Movie.fr.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nBonjour\n\n", encoding="utf-8"
    )

    resolution = await SourceResolver().resolve(
        mkv,
        preferred_languages=["en"],
        target_language="pt-PT",
    )
    assert resolution.selected is not None
    assert resolution.selected.type == SourceType.TRANSCRIPT

    selection = await AudioTrackSelector().select(mkv, preferred_languages=["en"])
    assert selection.selected is not None
    assert selection.selected.stream.comment is False

    wav = tmp_path / "selected.wav"
    await extract_audio(mkv, wav, stream_index=selection.selected.stream.stream_index)

    async def fake_transcribe_file(self, audio_path, **kwargs):
        return await _FakeASR().transcribe(audio_path, language=kwargs.get("language"))

    monkeypatch.setattr(TranscriptionService, "_transcribe_file", fake_transcribe_file)
    srt_path, transcript, *_rest = await TranscriptionService().transcribe_media_to_srt(
        mkv,
        tmp_path / "Movie.en.srt",
        provider="local",
        local_model="tiny",
        openai_key=None,
        source_language="en",
        preferred_languages=["en"],
    )
    assert srt_path.is_file()
    assert transcript.language == "en"
    assert transcript.language_confidence == 0.97

    pt_srt = tmp_path / "Movie.pt.srt"
    write_srt_atomic(
        pt_srt,
        SubtitleDocument(
            format="srt",
            encoding="utf-8",
            blocks=[
                SubtitleBlock(index=1, start="00:00:00,300", end="00:00:01,600", text="Olá do áudio inglês"),
            ],
        ),
        overwrite=True,
    )

    async def fake_ensure(**kwargs):
        return Path("/tmp/fake.onnx")

    async def fake_synth(self, text, voice, language, *, output_path, is_cancelled=None):
        from app.localization.artifacts import AudioArtifact
        from tests.fixtures.media import write_sine_wav

        write_sine_wav(Path(output_path), duration_s=0.8, frequency=220)
        return AudioArtifact(
            path=str(output_path),
            duration=0.8,
            sample_rate=16000,
            channels=1,
            provider="fake",
        )

    monkeypatch.setattr(
        "app.localization.dubbing.providers.piper.PiperTTSProvider.synthesize",
        fake_synth,
    )
    monkeypatch.setattr(
        "app.localization.dubbing.pipeline.ensure_piper_voice_available",
        fake_ensure,
    )
    monkeypatch.setattr("app.localization.dubbing.pipeline.load_piper_voice", lambda _path: object())

    event_log = JobEventLog(tmp_path / "job.jsonl", job_id=1)
    out = tmp_path / "Movie.pt.dub.mkv"
    artifact = await DubbingPipeline().run(
        media_path=mkv,
        source_srt_path=pt_srt,
        target_language="pt-PT",
        output_path=out,
        event_log=event_log,
        is_cancelled=lambda: False,
        use_loudnorm=False,
        mix_mode=DUB_MIX_VOICEOVER_PREVIEW,
    )
    assert out.is_file()
    assert artifact is not None
    verified = await probe_media_artifact(out)
    assert verified.verified is True
    assert verified.audio_streams >= 1
    assert verified.duration is not None and verified.duration > 0
