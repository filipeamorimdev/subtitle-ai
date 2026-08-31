"""Dubbing pipeline: speech segments → TTS → timing → timeline → mux."""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.logging import get_logger
from app.jobs.event_log import JobEventLog
from app.localization.artifacts import MediaArtifact
from app.localization.audio.models import AudioSeparationError
from app.localization.audio.separation import AudioSeparationService
from app.localization.dubbing.cache import DubCueCache, dub_cache_key
from app.localization.dubbing.dialogue import speech_segments_from_document
from app.localization.dubbing.mixer import (
    DUB_OUTPUT_SAMPLE_RATE,
    finalize_dialogue_track,
    mix_background_and_dialogue_track,
)
from app.localization.dubbing.models import VoiceConfig
from app.localization.dubbing.options import (
    DUB_MIX_BACKGROUND_PRESERVED,
    DUB_MIX_VOICEOVER_PREVIEW,
    normalize_dub_mix_mode,
    normalize_speaker_voice_overrides,
    cue_key,
    speaker_key,
)
from app.localization.dubbing.providers.chatterbox import (
    ChatterboxTTSProvider,
    TTSError,
    load_chatterbox_model,
    resolve_voice_model_for_language,
    resolve_voice_profile,
    tts_output_ignores_text,
    unload_chatterbox_model,
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


def _monotonic() -> float:
    return time.monotonic()


def _process_memory_stats() -> dict[str, float]:
    """Return current Linux process RSS/swap without adding a psutil dependency."""
    try:
        lines = Path("/proc/self/status").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, float] = {}
    for line in lines:
        key, _separator, raw = line.partition(":")
        if key not in {"VmRSS", "VmSwap"}:
            continue
        try:
            values[{"VmRSS": "rss_mb", "VmSwap": "swap_mb"}[key]] = round(
                float(raw.strip().split()[0]) / 1024.0, 1
            )
        except (IndexError, ValueError):
            continue
    return values


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
    dub_audio_wav: Path,
    output: Path,
    *,
    lang_tag: str,
    copy_original_audio: bool,
    dub_title: str = "Dub (TTS + background)",
) -> list[str]:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(media),
        "-i",
        str(dub_audio_wav),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a:0",
        "aac",
        "-b:a:0",
        "192k",
        "-disposition:a:0",
        "default",
        "-metadata:s:a:0",
        f"language={lang_tag}",
        "-metadata:s:a:0",
        f"title={dub_title}",
    ]
    if copy_original_audio:
        cmd.extend(
            [
                "-map",
                "0:a:0",
                "-c:a:1",
                "copy",
                "-disposition:a:1",
                "0",
                "-metadata:s:a:1",
                "title=Original audio",
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
        separation: AudioSeparationService | None = None,
    ) -> None:
        self.timing = timing or TimingEngine()
        self.translation = translation or DubTranslationService()
        self.separation = separation or AudioSeparationService()

    async def run(
        self,
        *,
        media_path: str | Path,
        source_srt_path: str | Path,
        target_language: str,
        output_path: str | Path,
        voice_model: str | None = None,
        mix_mode: str = DUB_MIX_BACKGROUND_PRESERVED,
        speaker_voice_overrides: dict[str, str] | None = None,
        event_log: JobEventLog,
        is_cancelled: CancelCheck,
        on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
        use_loudnorm: bool = True,
        cache_dir: str | Path | None = None,
        model_recycle_cues: int = 0,
        max_cue_seconds: float = 0.0,
    ) -> MediaArtifact:
        media = Path(media_path)
        source_srt = Path(source_srt_path)
        out = Path(output_path)
        if not media.is_file():
            raise DubError(f"Media file is not readable: {media}")
        if not source_srt.is_file():
            raise DubError(f"Source SRT is missing: {source_srt}")
        if out.exists() and out.stat().st_size > 0:
            raise DubError(f"Output dub already exists: {out.name}")
        try:
            mix_mode = normalize_dub_mix_mode(mix_mode)
        except ValueError as exc:
            raise DubError(str(exc)) from exc
        speaker_voice_overrides = normalize_speaker_voice_overrides(speaker_voice_overrides)

        event_log.record(event="started", media_path=str(media), target_language=target_language)
        try:
            content = source_srt.read_text(encoding="utf-8")
            document = parse_srt(content, encoding="utf-8")
        except Exception as exc:
            raise DubError(f"Failed to parse SRT {source_srt.name}: {exc}") from exc

        segments = speech_segments_from_document(document)
        labelled_speakers = sorted({segment.speaker_id for segment in segments if segment.speaker_id})
        event_log.record(
            event="source_srt",
            path=str(source_srt),
            cue_count=len(document.blocks),
            speech_segments=len(segments),
            labelled_speakers=labelled_speakers,
            mix_mode=mix_mode,
        )
        logger.info("dub_speech_segments count=%s cues=%s", len(segments), len(document.blocks))

        try:
            voice_model = resolve_voice_profile(
                voice_model or resolve_voice_model_for_language(target_language),
                target_language,
            ).id
        except TTSError as exc:
            raise DubError(str(exc)) from exc
        tts_by_model: dict[str, ChatterboxTTSProvider] = {}
        chatterbox_model = None
        cue_cache = (
            DubCueCache(
                Path(cache_dir),
                dub_cache_key(
                    source_srt=content,
                    target_language=target_language,
                    voice_model=voice_model,
                    speaker_voice_overrides=speaker_voice_overrides,
                    timing=self.timing,
                ),
            )
            if cache_dir is not None
            else None
        )

        async def tts_for(model: str) -> ChatterboxTTSProvider:
            profile = resolve_voice_profile(model, target_language)
            cached = tts_by_model.get(profile.id)
            if cached is not None:
                return cached
            try:
                nonlocal chatterbox_model
                if is_cancelled():
                    raise TTSError("Dub cancelled")
                if chatterbox_model is None:
                    chatterbox_model = await asyncio.to_thread(load_chatterbox_model)
            except TTSError as exc:
                raise DubError(str(exc)) from exc
            tts = ChatterboxTTSProvider(chatterbox_model, profile)
            tts_by_model[profile.id] = tts
            event_log.record(
                event="voice_loaded",
                voice_model=profile.id,
                provider="chatterbox",
                device=getattr(chatterbox_model, "device", "unknown"),
            )
            return tts

        if is_cancelled():
            event_log.record(event="cancelled")
            return None

        total = max(1, len(segments))
        synth_samples_by_model: dict[str, list[tuple[int, float]]] = {}
        speed_adjustments = 0
        timeline = AudioTimeline()
        assigned_speakers: dict[str, str] = {}
        used_voice_models: set[str] = set()
        synthesized_since_recycle = 0

        with tempfile.TemporaryDirectory(prefix="subtitle-ai-dub-") as tmp:
            tmp_dir = Path(tmp)
            for index, segment in enumerate(segments, start=1):
                if is_cancelled():
                    event_log.record(event="cancelled")
                    return None
                text = segment.spoken_text
                cue_wav = tmp_dir / f"seg-{index}.wav"
                segment_cue_key = cue_key(segment.source_cues[0] if segment.source_cues else None)
                override_key = segment_cue_key if segment_cue_key in speaker_voice_overrides else speaker_key(segment.speaker_id)
                segment_voice_model = speaker_voice_overrides.get(override_key, voice_model)
                profile = resolve_voice_profile(segment_voice_model, target_language)
                segment_voice_model = profile.id
                used_voice_models.add(segment_voice_model)
                voice_config = VoiceConfig(
                    voice_id=segment_voice_model,
                    language=target_language,
                    speaker_id=segment.speaker_id,
                )
                if segment.speaker_id and segment.speaker_id not in assigned_speakers:
                    assigned_speakers[segment.speaker_id] = segment_voice_model
                    event_log.record(
                        event="speaker_voice_assigned",
                        speaker_id=segment.speaker_id,
                        voice_model=segment_voice_model,
                        source="override" if override_key in speaker_voice_overrides else "default",
                    )
                event_log.record(
                    event="speech",
                    index=index,
                    start=segment.start,
                    end=segment.end,
                    chars=len(text),
                    speaker_id=segment.speaker_id,
                    voice_model=segment_voice_model,
                    text_preview=text[:80],
                )
                cached_cue = cue_cache.load(index) if cue_cache is not None else None
                if cached_cue is not None:
                    actual = cached_cue.actual
                    decision = cached_cue.decision
                    if decision.speed > 1.001:
                        speed_adjustments += 1
                    if actual:
                        synth_samples_by_model.setdefault(segment_voice_model, []).append((len(text), actual))
                    timeline.add_clip(cached_cue.path, segment.start, speaker_id=segment.speaker_id)
                    event_log.record(
                        event="speech_cached",
                        index=index,
                        path=str(cached_cue.path),
                        **_process_memory_stats(),
                    )
                    if on_progress:
                        maybe = on_progress(index, total)
                        if maybe is not None:
                            await maybe
                    continue

                tts = await tts_for(segment_voice_model)
                synthesis_started = _monotonic()
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
                    synth_samples_by_model.setdefault(segment_voice_model, []).append((len(text), actual))
                checkpoint = (
                    cue_cache.store(index, shaped, actual=actual, decision=decision)
                    if cue_cache is not None
                    else shaped
                )
                timeline.add_clip(checkpoint, segment.start, speaker_id=segment.speaker_id)
                synthesis_seconds = _monotonic() - synthesis_started
                event_log.record(
                    event="synthesis_completed",
                    index=index,
                    seconds=round(synthesis_seconds, 3),
                    checkpointed=cue_cache is not None,
                    **_process_memory_stats(),
                )
                if on_progress:
                    maybe = on_progress(index, total)
                    if maybe is not None:
                        await maybe
                synthesized_since_recycle += 1
                if max_cue_seconds > 0 and synthesis_seconds > max_cue_seconds:
                    device = str(getattr(chatterbox_model, "device", "cpu"))
                    del tts
                    tts_by_model.clear()
                    chatterbox_model = None
                    await asyncio.to_thread(unload_chatterbox_model, device=device)
                    event_log.record(
                        event="voice_recycled",
                        index=index,
                        device=device,
                        reason="slow_cue",
                        seconds=round(synthesis_seconds, 3),
                    )
                    raise DubError(
                        f"Chatterbox slowed to {synthesis_seconds / 60.0:.1f} minutes "
                        f"for cue {index}. The completed cue was checkpointed; retry "
                        "the job to resume with a fresh model."
                    )
                if (
                    model_recycle_cues > 0
                    and synthesized_since_recycle >= model_recycle_cues
                    and index < total
                    and chatterbox_model is not None
                ):
                    device = str(getattr(chatterbox_model, "device", "cpu"))
                    del tts
                    tts_by_model.clear()
                    chatterbox_model = None
                    await asyncio.to_thread(unload_chatterbox_model, device=device)
                    synthesized_since_recycle = 0
                    event_log.record(
                        event="voice_recycled", index=index, device=device, reason="periodic"
                    )

            if not timeline.clips:
                raise DubError("No cue text was synthesized; dub output would be silence.")
            if any(tts_output_ignores_text(samples) for samples in synth_samples_by_model.values()):
                raise DubError(
                    "Chatterbox produced nearly the same clip length for every subtitle. "
                    "The voice is not reading cue text; refusing to mux a looping track."
                )

            media_duration_s = await probe_duration_seconds(media)
            event_log.record(
                event="mix",
                input_clips=len(timeline.clips),
                voice_model=voice_model,
                voice_models=sorted(used_voice_models),
                speaker_voices=assigned_speakers,
                mix_mode=mix_mode,
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
            try:
                await finalize_dialogue_track(
                    tts_audio_wav,
                    use_loudnorm=use_loudnorm,
                    output_sample_rate=DUB_OUTPUT_SAMPLE_RATE,
                    is_cancelled=is_cancelled,
                )
            except ProcessError as exc:
                if exc.outcome is ProcessOutcome.CANCELLED or is_cancelled():
                    event_log.record(event="cancelled")
                    return None
                raise DubError(str(exc)) from exc
            event_log.record(
                event="dialogue_audio_ready",
                path=str(tts_audio_wav),
                duration=wav_duration_seconds(tts_audio_wav),
                sample_rate=DUB_OUTPUT_SAMPLE_RATE,
            )

            copy_original_audio = await probe_has_audio_stream(media)
            audio_for_mux = tts_audio_wav
            effective_mix_mode = mix_mode
            if mix_mode == DUB_MIX_BACKGROUND_PRESERVED and copy_original_audio:
                stems_dir = tmp_dir / "stems"

                def record_separation_event(event: str, payload: dict[str, object]) -> None:
                    event_log.record(event=event, **payload)

                event_log.record(event="background_separation_start", mode=mix_mode)
                try:
                    separation = await self.separation.separate(
                        media,
                        output_dir=stems_dir,
                        task_id=f"dub-{getattr(event_log, 'job_id', 'media')}",
                        is_cancelled=is_cancelled,
                        event_cb=record_separation_event,
                    )
                    background_wav = Path(separation.background.path)
                    mixed_wav = tmp_dir / "dub-mixed.wav"
                    audio_for_mux = await mix_background_and_dialogue_track(
                        background_wav,
                        tts_audio_wav,
                        mixed_wav,
                        use_loudnorm=use_loudnorm,
                        is_cancelled=is_cancelled,
                    )
                except AudioSeparationError as exc:
                    if is_cancelled():
                        event_log.record(event="cancelled")
                        return None
                    raise DubError(f"Could not create a background-preserved dub: {exc}") from exc
                except ProcessError as exc:
                    if exc.outcome is ProcessOutcome.CANCELLED or is_cancelled():
                        event_log.record(event="cancelled")
                        return None
                    raise DubError(str(exc)) from exc
                except RuntimeError as exc:
                    raise DubError(str(exc)) from exc
                event_log.record(
                    event="background_mix_ready",
                    path=str(audio_for_mux),
                    duration=wav_duration_seconds(audio_for_mux),
                    provider=separation.provider,
                    model=separation.model,
                    source_stream=separation.metadata.get("selected_stream"),
                )
            elif mix_mode == DUB_MIX_BACKGROUND_PRESERVED:
                # A video without audio has no bed to preserve.  It can still produce
                # a usable voiceover-preview track and the event log explains why.
                effective_mix_mode = DUB_MIX_VOICEOVER_PREVIEW
                event_log.record(
                    event="background_mix_unavailable",
                    reason="source_media_has_no_audio",
                    fallback_mode=effective_mix_mode,
                )
            event_log.record(
                event="audio_ready",
                path=str(audio_for_mux),
                duration=wav_duration_seconds(audio_for_mux),
                mix_mode=effective_mix_mode,
                sample_rate=DUB_OUTPUT_SAMPLE_RATE,
            )

            event_log.record(event="mux_start", output_path=str(out))
            tmp_out = tmp_dir / out.name
            lang_tag = sidecar_language_tag(target_language)
            mux_cmd = build_mux_command(
                media,
                audio_for_mux,
                tmp_out,
                lang_tag=lang_tag,
                copy_original_audio=copy_original_audio,
                dub_title=(
                    "Dub (TTS + background)"
                    if effective_mix_mode == DUB_MIX_BACKGROUND_PRESERVED
                    else "Dub (TTS preview)"
                ),
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
                chatterbox_inputs=len(timeline.clips),
                verified=verified.verified,
                duration=verified.duration,
            )
            logger.info(
                "dub_completed output=%s duration=%s verified=%s",
                out,
                verified.duration,
                verified.verified,
            )
            if cue_cache is not None and verified.verified:
                cue_cache.clear()
            return verified


async def dub_media_from_srt_to_mkv(
    *,
    media_path: str | Path,
    source_srt_path: str | Path,
    target_language: str,
    output_path: str | Path,
    voice_model: str | None = None,
    mix_mode: str = DUB_MIX_BACKGROUND_PRESERVED,
    speaker_voice_overrides: dict[str, str] | None = None,
    event_log: JobEventLog,
    is_cancelled: CancelCheck,
    on_progress: Callable[[int, int], Awaitable[None] | None] | None = None,
    cache_dir: str | Path | None = None,
    model_recycle_cues: int = 0,
    max_cue_seconds: float = 0.0,
) -> None:
    await DubbingPipeline().run(
        media_path=media_path,
        source_srt_path=source_srt_path,
        target_language=target_language,
        output_path=output_path,
        voice_model=voice_model,
        mix_mode=mix_mode,
        speaker_voice_overrides=speaker_voice_overrides,
        event_log=event_log,
        is_cancelled=is_cancelled,
        on_progress=on_progress,
        cache_dir=cache_dir,
        model_recycle_cues=model_recycle_cues,
        max_cue_seconds=max_cue_seconds,
    )
