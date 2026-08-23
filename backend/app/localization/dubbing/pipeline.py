"""Dubbing pipeline: speech segments → TTS → timing → timeline → mux."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.logging import get_logger
from app.jobs.event_log import JobEventLog
from app.localization.artifacts import MediaArtifact
from app.localization.dubbing.dialogue import speech_segments_from_document
from app.localization.dubbing.mixer import finalize_dialogue_track
from app.localization.dubbing.models import VoiceConfig
from app.localization.dubbing.providers.piper import (
    PiperTTSProvider,
    TTSError,
    ensure_piper_voice_available,
    load_piper_voice,
    piper_output_ignores_text,
    resolve_voice_model_for_language,
    wav_duration_seconds,
)
from app.localization.dubbing.timeline import AudioTimeline, CUE_SAMPLE_RATE
from app.localization.dubbing.timing import TimingEngine
from app.localization.dubbing.translation import DubTranslationService
from app.media.process_runner import ProcessError, ProcessOutcome, run_process_checked
from app.subtitles.filenames import sidecar_language_tag
from app.subtitles.parsers.srt import parse_srt

logger = get_logger("dubbing.pipeline")

CancelCheck = Callable[[], bool]


class DubError(Exception):
    pass


async def probe_has_audio_stream(path: str | Path) -> bool:
    try:
        result = await run_process_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                str(path),
            ],
            timeout_s=60.0,
        )
    except ProcessError:
        return False
    try:
        payload = json.loads(result.stdout_text or "{}")
    except json.JSONDecodeError:
        return False
    return bool(payload.get("streams"))


async def probe_duration_seconds(path: str | Path) -> float | None:
    try:
        result = await run_process_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            timeout_s=60.0,
        )
    except ProcessError as exc:
        raise DubError(str(exc)) from exc
    try:
        payload = json.loads(result.stdout_text or "{}")
        duration = float((payload.get("format") or {}).get("duration"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not math.isfinite(duration) or duration <= 0:
        return None
    return duration


async def probe_media_artifact(path: str | Path) -> MediaArtifact:
    media = Path(path)
    duration = await probe_duration_seconds(media)
    audio_ok = await probe_has_audio_stream(media)
    exists = media.is_file() and media.stat().st_size > 0
    verified = exists and audio_ok and duration is not None
    logger.info(
        "media_verified path=%s exists=%s audio=%s duration=%s",
        media,
        exists,
        audio_ok,
        duration,
    )
    return MediaArtifact(
        path=str(media),
        duration=duration,
        audio_streams=1 if audio_ok else 0,
        verified=verified,
        metadata={"size": media.stat().st_size if exists else 0},
    )


def build_mux_command(
    media: Path,
    tts_audio_wav: Path,
    output: Path,
    *,
    lang_tag: str,
    copy_original_audio: bool,
) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(media),
        "-i",
        str(tts_audio_wav),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
    ]
    if copy_original_audio:
        cmd.extend(
            [
                "-map",
                "0:a:0",
                "-map",
                "1:a:0",
                "-c:a:0",
                "copy",
                "-c:a:1",
                "aac",
                "-b:a:1",
                "192k",
                "-metadata:s:a:1",
                f"language={lang_tag}",
                "-metadata:s:a:1",
                "title=Dub (TTS)",
            ]
        )
    else:
        cmd.extend(
            [
                "-map",
                "1:a:0",
                "-c:a:0",
                "aac",
                "-b:a:0",
                "192k",
                "-metadata:s:a:0",
                f"language={lang_tag}",
                "-metadata:s:a:0",
                "title=Dub (TTS)",
            ]
        )
    cmd.append(str(output))
    return cmd


async def shape_clip(
    input_wav: Path,
    output_wav: Path,
    *,
    speed: float,
    is_cancelled: CancelCheck | None = None,
) -> None:
    filters = ["aresample=" + str(CUE_SAMPLE_RATE)]
    if speed > 1.001:
        filters.insert(0, f"atempo={speed:.4f}")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(input_wav),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(CUE_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        "-af",
        ",".join(filters),
        str(output_wav),
    ]
    try:
        await run_process_checked(
            cmd,
            timeout_s=1800.0,
            is_cancelled=is_cancelled,
            output_paths=[output_wav],
        )
    except ProcessError as exc:
        if exc.outcome is ProcessOutcome.CANCELLED:
            raise DubError("Dub cancelled") from exc
        raise DubError(str(exc)) from exc


class DubbingPipeline:
    def __init__(
        self,
        *,
        timing: TimingEngine | None = None,
        translation: DubTranslationService | None = None,
    ) -> None:
        self.timing = timing or TimingEngine()
        self.translation = translation or DubTranslationService()

    async def run(
        self,
        *,
        media_path: str | Path,
        source_srt_path: str | Path,
        target_language: str,
        output_path: str | Path,
        voice_model: str | None = None,
        event_log: JobEventLog,
        is_cancelled: CancelCheck,
        on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
        voices_dir: Path | None = None,
        use_loudnorm: bool = True,
    ) -> MediaArtifact:
        media = Path(media_path)
        source_srt = Path(source_srt_path)
        out = Path(output_path)
        voices_dir = voices_dir or Path("/config/piper-voices")

        if not media.is_file():
            raise DubError(f"Media file is not readable: {media}")
        if not source_srt.is_file():
            raise DubError(f"Source SRT is missing: {source_srt}")
        if out.exists() and out.stat().st_size > 0:
            raise DubError(f"Output dub already exists: {out.name}")

        event_log.record(event="started", media_path=str(media), target_language=target_language)
        try:
            content = source_srt.read_text(encoding="utf-8")
            document = parse_srt(content, encoding="utf-8")
        except Exception as exc:
            raise DubError(f"Failed to parse SRT {source_srt.name}: {exc}") from exc

        segments = speech_segments_from_document(document)
        event_log.record(event="source_srt", path=str(source_srt), cue_count=len(document.blocks), speech_segments=len(segments))
        logger.info("dub_speech_segments count=%s cues=%s", len(segments), len(document.blocks))

        voice_model = voice_model or resolve_voice_model_for_language(target_language)
        model_path = await ensure_piper_voice_available(
            voice_model=voice_model,
            voices_dir=voices_dir,
            is_cancelled=is_cancelled,
        )
        if is_cancelled():
            event_log.record(event="cancelled")
            return None
        voice = await asyncio.to_thread(load_piper_voice, model_path)
        tts = PiperTTSProvider(voice)
        voice_config = VoiceConfig(voice_id=voice_model, language=target_language)
        event_log.record(event="voice_loaded", voice_model=voice_model, path=str(model_path))

        total = max(1, len(segments))
        synth_samples: list[tuple[int, float]] = []
        speed_adjustments = 0
        timeline = AudioTimeline()

        with tempfile.TemporaryDirectory(prefix="subtitle-ai-dub-") as tmp:
            tmp_dir = Path(tmp)
            for index, segment in enumerate(segments, start=1):
                if is_cancelled():
                    event_log.record(event="cancelled")
                    return None
                text = segment.spoken_text
                cue_wav = tmp_dir / f"seg-{index}.wav"
                event_log.record(
                    event="speech",
                    index=index,
                    start=segment.start,
                    end=segment.end,
                    chars=len(text),
                    speaker_id=segment.speaker_id,
                    text_preview=text[:80],
                )
                try:
                    artifact = await tts.synthesize(
                        text,
                        voice_config,
                        target_language,
                        output_path=cue_wav,
                        is_cancelled=is_cancelled,
                    )
                except TTSError as exc:
                    raise DubError(str(exc)) from exc

                actual = artifact.duration or wav_duration_seconds(cue_wav) or 0.0
                decision = self.timing.decide(actual=actual, available=segment.duration)
                event_log.record(event="timing", index=index, **decision.to_dict())

                if decision.action == "adapt":
                    adapted = await self.translation.adapt_if_needed(
                        text,
                        target_language=target_language,
                        available_duration=segment.duration,
                        speaker_id=segment.speaker_id,
                    )
                    if adapted != text:
                        segment.adapted_text = adapted
                        try:
                            artifact = await tts.synthesize(
                                adapted,
                                voice_config,
                                target_language,
                                output_path=cue_wav,
                                is_cancelled=is_cancelled,
                            )
                        except TTSError as exc:
                            raise DubError(str(exc)) from exc
                        actual = artifact.duration or wav_duration_seconds(cue_wav) or 0.0
                        decision = self.timing.decide(actual=actual, available=segment.duration)
                        event_log.record(event="timing_after_adapt", index=index, **decision.to_dict())

                shaped = tmp_dir / f"seg-{index}-shaped.wav"
                speed = decision.speed if decision.action == "speed" else 1.0
                if speed > 1.001:
                    speed_adjustments += 1
                await shape_clip(cue_wav, shaped, speed=speed, is_cancelled=is_cancelled)
                if actual:
                    synth_samples.append((len(text), actual))
                timeline.add_clip(shaped, segment.start, speaker_id=segment.speaker_id)
                if on_progress:
                    maybe = on_progress(index, total)
                    if maybe is not None:
                        await maybe

            if not timeline.clips:
                raise DubError("No cue text was synthesized; dub output would be silence.")
            if piper_output_ignores_text(synth_samples):
                raise DubError(
                    "Piper produced nearly the same clip length for every subtitle. "
                    "The voice is not reading cue text; refusing to mux a looping track."
                )

            media_duration_s = await probe_duration_seconds(media)
            event_log.record(
                event="mix",
                input_clips=len(timeline.clips),
                voice_model=voice_model,
                media_duration_s=media_duration_s,
                overlaps=timeline.overlap_count,
                speed_adjustments=speed_adjustments,
                translation_adaptations=self.translation.adapt_count,
            )
            logger.info(
                "dub_mix clips=%s overlaps=%s speed_adjustments=%s adaptations=%s",
                len(timeline.clips),
                timeline.overlap_count,
                speed_adjustments,
                self.translation.adapt_count,
            )
            tts_audio_wav = tmp_dir / "tts-audio.wav"
            if is_cancelled():
                event_log.record(event="cancelled")
                return None
            timeline.render(tts_audio_wav, media_duration_s=media_duration_s)
            await finalize_dialogue_track(
                tts_audio_wav,
                use_loudnorm=use_loudnorm,
                is_cancelled=is_cancelled,
            )
            event_log.record(event="audio_ready", path=str(tts_audio_wav), duration=wav_duration_seconds(tts_audio_wav))

            event_log.record(event="mux_start", output_path=str(out))
            tmp_out = tmp_dir / out.name
            lang_tag = sidecar_language_tag(target_language)
            copy_original_audio = await probe_has_audio_stream(media)
            mux_cmd = build_mux_command(
                media,
                tts_audio_wav,
                tmp_out,
                lang_tag=lang_tag,
                copy_original_audio=copy_original_audio,
            )
            try:
                await run_process_checked(
                    mux_cmd,
                    timeout_s=12 * 3600.0,
                    is_cancelled=is_cancelled,
                    output_paths=[tmp_out],
                )
            except ProcessError as exc:
                if exc.outcome is ProcessOutcome.CANCELLED:
                    event_log.record(event="cancelled")
                    return None
                raise DubError(str(exc)) from exc
            if is_cancelled():
                event_log.record(event="cancelled")
                return None
            if not tmp_out.is_file() or tmp_out.stat().st_size <= 0:
                raise DubError("Mux step produced no output.")

            staging = out.with_name(f".{out.name}.tmp")
            try:
                shutil.copyfile(tmp_out, staging)
                staging.replace(out)
            finally:
                if staging.exists():
                    try:
                        staging.unlink()
                    except OSError:
                        pass

            verified = await probe_media_artifact(out)
            event_log.record(
                event="completed",
                output_path=str(out),
                piper_inputs=len(timeline.clips),
                verified=verified.verified,
                duration=verified.duration,
            )
            logger.info(
                "dub_completed output=%s duration=%s verified=%s",
                out,
                verified.duration,
                verified.verified,
            )
            return verified


async def dub_media_from_srt_to_mkv(
    *,
    media_path: str | Path,
    source_srt_path: str | Path,
    target_language: str,
    output_path: str | Path,
    voice_model: str | None = None,
    event_log: JobEventLog,
    is_cancelled: CancelCheck,
    on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
) -> None:
    await DubbingPipeline().run(
        media_path=media_path,
        source_srt_path=source_srt_path,
        target_language=target_language,
        output_path=output_path,
        voice_model=voice_model,
        event_log=event_log,
        is_cancelled=is_cancelled,
        on_progress=on_progress,
    )
