"""AI-assisted speaker grouping for a manually requested dub.

The analysis is deliberately an on-demand preview instead of part of the
dubbing worker.  It lets the person requesting a dub inspect and change every
suggestion before any generated speech is queued.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.ai.bootstrap import bootstrap_providers
from app.ai.errors import AIProviderError
from app.ai.providers.registry import get_provider_registry
from app.db.models import MediaItemRow
from app.integrations.bazarr.paths import apply_path_mapping, is_under_roots, mappings_from_settings
from app.localization.dubbing.dialogue import speech_segments_from_document
from app.localization.dubbing.providers.piper import resolve_voice_model_for_language
from app.media.process_runner import ProcessError, run_process_checked
from app.services.ai_usage import AiUsageService, RecordingAIProvider
from app.services.model_catalog import ModelCatalogService, check_audio_analysis_compatibility
from app.services.model_preferences import AUDIO_ANALYSIS_PURPOSE, list_preferences
from app.services.settings import SettingsService
from app.subtitles.filenames import find_existing_sidecar
from app.subtitles.parsers.srt import SrtParseError, parse_srt


MAX_SAMPLE_CLIPS = 14
MAX_SAMPLE_DURATION_SECONDS = 56.0
MAX_CLIP_DURATION_SECONDS = 5.0
AUDIO_SAMPLE_RATE = 16_000


class VoiceCastError(ValueError):
    """A user-facing failure while preparing an AI voice-cast proposal."""


@dataclass(frozen=True)
class VoiceCastSuggestion:
    speaker_id: str
    voice_style: str
    cue_indices: list[int]
    confidence: float | None
    voice_model: str


@dataclass(frozen=True)
class VoiceCastResult:
    provider_id: str
    model_id: str
    suggestions: list[VoiceCastSuggestion]
    analysed_cue_count: int
    metadata_used: dict[str, str | int]


@dataclass(frozen=True)
class _SampledCue:
    index: int
    start: float
    duration: float
    text: str


def _select_sample_cues(segments: list[Any]) -> list[_SampledCue]:
    """Pick compact, evenly distributed dialogue samples from an SRT timeline."""
    candidates = [segment for segment in segments if segment.source_cues and segment.duration >= 0.2]
    if not candidates:
        return []

    chosen_positions: list[int]
    if len(candidates) <= MAX_SAMPLE_CLIPS:
        chosen_positions = list(range(len(candidates)))
    else:
        # Include both ends and spread the remaining samples through the title.
        chosen_positions = sorted(
            {
                round(position * (len(candidates) - 1) / (MAX_SAMPLE_CLIPS - 1))
                for position in range(MAX_SAMPLE_CLIPS)
            }
        )

    remaining = MAX_SAMPLE_DURATION_SECONDS
    selected: list[_SampledCue] = []
    for position in chosen_positions:
        segment = candidates[position]
        duration = min(MAX_CLIP_DURATION_SECONDS, max(0.8, segment.duration), remaining)
        if duration < 0.5:
            break
        selected.append(
            _SampledCue(
                index=int(segment.source_cues[0]),
                start=float(segment.start),
                duration=duration,
                text=str(segment.spoken_text)[:280],
            )
        )
        remaining -= duration
        if remaining < 0.5:
            break
    return selected


def _metadata_context(media: MediaItemRow) -> dict[str, str | int]:
    """Return only small, useful Bazarr metadata; never fetch third-party pages."""
    result: dict[str, str | int] = {}
    if media.year:
        result["year"] = media.year
    if media.season is not None:
        result["season"] = media.season
    if media.episode is not None:
        result["episode"] = media.episode
    if media.episode_title:
        result["episode_title"] = media.episode_title

    values = media.metadata_json if isinstance(media.metadata_json, dict) else {}
    aliases = {
        "imdb_id": ("imdb_id", "imdbId", "imdb", "imdbid"),
        "tmdb_id": ("tmdb_id", "tmdbId", "tmdb", "tmdbid"),
        "tvdb_id": ("tvdb_id", "tvdbId", "tvdb", "tvdbid"),
        "overview": ("overview", "plot", "description"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = values.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                result[target] = str(value)[:800] if target == "overview" else value
                break
    return result


def _json_object(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise VoiceCastError("The audio model did not return a usable voice-cast proposal.") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise VoiceCastError("The audio model did not return valid voice-cast JSON.") from exc
    if not isinstance(value, dict):
        raise VoiceCastError("The audio model returned an invalid voice-cast proposal.")
    return value


def _suggestions_from_response(
    content: str,
    *,
    allowed_cues: set[int],
    default_voice_model: str,
) -> list[VoiceCastSuggestion]:
    payload = _json_object(content)
    raw_speakers = payload.get("speakers")
    if not isinstance(raw_speakers, list):
        raise VoiceCastError("The audio model response did not include any speaker suggestions.")

    suggestions: list[VoiceCastSuggestion] = []
    already_assigned: set[int] = set()
    for number, raw in enumerate(raw_speakers, start=1):
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("speaker_id") or raw.get("name")
        speaker_id = re.sub(r"\s+", " ", str(raw_id or "")).strip()[:80]
        if not speaker_id:
            speaker_id = f"Speaker {number}"
        raw_indices = raw.get("cue_indices")
        indices: list[int] = []
        if isinstance(raw_indices, list):
            for value in raw_indices:
                try:
                    cue_index = int(value)
                except (TypeError, ValueError):
                    continue
                if cue_index in allowed_cues and cue_index not in already_assigned:
                    indices.append(cue_index)
                    already_assigned.add(cue_index)
        if not indices:
            continue
        style = re.sub(r"\s+", " ", str(raw.get("voice_style") or "Neutral dialogue voice")).strip()
        style = style[:180] or "Neutral dialogue voice"
        confidence: float | None = None
        try:
            raw_confidence = raw.get("confidence")
            if raw_confidence is not None:
                confidence = max(0.0, min(1.0, float(raw_confidence)))
        except (TypeError, ValueError):
            pass
        suggestions.append(
            VoiceCastSuggestion(
                speaker_id=speaker_id,
                voice_style=style,
                cue_indices=sorted(indices),
                confidence=confidence,
                voice_model=default_voice_model,
            )
        )

    # The model is instructed to assign every cue, but retaining uncovered cues
    # means a partial result is still safely usable and visibly editable.
    for cue_index in sorted(allowed_cues - already_assigned):
        suggestions.append(
            VoiceCastSuggestion(
                speaker_id="Unidentified speaker",
                voice_style="Use the language default until you choose another Piper voice",
                cue_indices=[cue_index],
                confidence=None,
                voice_model=default_voice_model,
            )
        )
    if not suggestions:
        raise VoiceCastError("The audio model could not assign any sampled dialogue to a speaker.")
    return suggestions


def _audio_prompt(
    *,
    media: MediaItemRow,
    target_language: str,
    sampled_cues: list[_SampledCue],
    metadata: dict[str, str | int],
) -> str:
    cue_lines = "\n".join(
        f"- cue {cue.index} at {cue.start:.1f}s: {cue.text}" for cue in sampled_cues
    )
    metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else "{}"
    return f"""You are an audio casting assistant for a TV or film dub. Analyse the attached WAV, which
contains short dialogue clips in exactly the order listed below. Group clips that sound like the
same speaker. The subtitle text is contextual only and may already be translated into {target_language}.

Title: {media.title}
Media type: {media.media_type}
Bazarr metadata and external IDs (when supplied): {metadata_json}

Sampled clips:
{cue_lines}

Return JSON only in this exact shape:
{{"speakers":[{{"speaker_id":"Speaker 1","voice_style":"brief description of the vocal character","cue_indices":[12,48],"confidence":0.0}}]}}

Rules: assign every listed cue index exactly once; do not claim to identify a real actor or
character unless an explicit subtitle label gives the name; use stable generic names otherwise;
describe vocal character rather than stereotypes; keep confidence between 0 and 1."""


async def _build_audio_sample(media_path: Path, sampled_cues: list[_SampledCue]) -> bytes:
    with tempfile.TemporaryDirectory(prefix="subtitle-ai-voice-cast-") as directory:
        tmp_dir = Path(directory)
        clips: list[Path] = []
        for count, cue in enumerate(sampled_cues, start=1):
            output = tmp_dir / f"clip-{count}.wav"
            try:
                await run_process_checked(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-ss",
                        f"{cue.start:.3f}",
                        "-t",
                        f"{cue.duration:.3f}",
                        "-i",
                        str(media_path),
                        "-map",
                        "0:a:0?",
                        "-ac",
                        "1",
                        "-ar",
                        str(AUDIO_SAMPLE_RATE),
                        "-c:a",
                        "pcm_s16le",
                        "-y",
                        str(output),
                    ],
                    timeout_s=30,
                    output_paths=[output],
                )
            except ProcessError as exc:
                raise VoiceCastError(f"Could not prepare dialogue audio for analysis: {exc}") from exc
            if output.exists() and output.stat().st_size > 44:
                clips.append(output)
        if not clips:
            raise VoiceCastError("No dialogue audio could be extracted for voice analysis.")

        # Do not join WAV payloads with Python's ``wave`` module here.  FFmpeg
        # can emit WAV variants (including extensible headers) that Python's
        # reader does not consistently accept across the supported runtimes.
        # It already owns both extraction and encoding, so let it concatenate
        # the normalised clips as well.
        combined = tmp_dir / "voice-cast-sample.wav"
        command = ["ffmpeg", "-nostdin", "-v", "error"]
        for clip in clips:
            command.extend(["-i", str(clip)])
        inputs = "".join(f"[{index}:a]" for index in range(len(clips)))
        command.extend(
            [
                "-filter_complex",
                f"{inputs}concat=n={len(clips)}:v=0:a=1[combined]",
                "-map",
                "[combined]",
                "-ac",
                "1",
                "-ar",
                str(AUDIO_SAMPLE_RATE),
                "-c:a",
                "pcm_s16le",
                "-y",
                str(combined),
            ]
        )
        try:
            await run_process_checked(command, timeout_s=30, output_paths=[combined])
            return combined.read_bytes()
        except (OSError, ProcessError) as exc:
            raise VoiceCastError(f"Could not combine dialogue audio for analysis: {exc}") from exc


class VoiceCastService:
    def __init__(self, db: Session) -> None:
        self.db = db

    async def suggest(self, media: MediaItemRow, *, target_language: str) -> VoiceCastResult:
        public = SettingsService(self.db).get_public()
        mappings = mappings_from_settings([mapping.model_dump() for mapping in public.path_mappings])
        media_path = Path(apply_path_mapping(media.path, mappings)) if media.path else None
        if media_path is None or not media_path.exists():
            raise VoiceCastError("Media file is not readable on disk.")
        if not is_under_roots(media_path, public.media_roots):
            raise VoiceCastError("Media path is outside configured media roots.")
        subtitle_path = find_existing_sidecar(media_path, target_language)
        if subtitle_path is None:
            raise VoiceCastError("Create the localized subtitle before auto-casting voices.")
        try:
            document = parse_srt(subtitle_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SrtParseError) as exc:
            raise VoiceCastError("Could not read the localized subtitle for voice analysis.") from exc
        sampled_cues = _select_sample_cues(speech_segments_from_document(document))
        if not sampled_cues:
            raise VoiceCastError("The localized subtitle has no dialogue cues to analyse.")
        audio_bytes = await _build_audio_sample(media_path, sampled_cues)

        bootstrap_providers(self.db)
        registry = get_provider_registry()
        catalog = ModelCatalogService(self.db)
        prefs = list_preferences(
            self.db,
            enabled_only=True,
            purpose=AUDIO_ANALYSIS_PURPOSE,
        )
        usable: list[Any] = []
        for preference in prefs:
            model = catalog.get_model(preference.provider_id, preference.model_id)
            if model is None or not model.available or model.is_past_sunset():
                continue
            compatible, _reason = check_audio_analysis_compatibility(model)
            provider = registry.get_optional(preference.provider_id)
            if compatible and provider is not None and provider.is_configured():
                usable.append(preference)
        if not usable:
            raise VoiceCastError(
                "Add and enable an audio-capable model in Settings → AI Models → Audio Analysis."
            )

        metadata = _metadata_context(media)
        prompt = _audio_prompt(
            media=media,
            target_language=target_language,
            sampled_cues=sampled_cues,
            metadata=metadata,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": "Return concise, valid JSON only. Do not add Markdown fences.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                            "format": "wav",
                        },
                    },
                ],
            },
        ]
        failures: list[str] = []
        default_voice = resolve_voice_model_for_language(target_language)
        for preference in usable:
            provider = registry.get_optional(preference.provider_id)
            if provider is None:
                continue
            tracked = RecordingAIProvider(
                provider,
                AiUsageService(self.db),
                job_id=None,
                trigger_type="manual",
                default_operation="audio_analysis",
                tier=preference.tier,
                provider_id=preference.provider_id,
            )
            try:
                response = await tracked.chat_completion(
                    messages=messages,
                    model_id=preference.model_id,
                    temperature=0,
                    max_tokens=900,
                )
                suggestions = _suggestions_from_response(
                    response.content,
                    allowed_cues={cue.index for cue in sampled_cues},
                    default_voice_model=default_voice,
                )
                return VoiceCastResult(
                    provider_id=response.provider_id,
                    model_id=response.model_id,
                    suggestions=suggestions,
                    analysed_cue_count=len(sampled_cues),
                    metadata_used=metadata,
                )
            except (AIProviderError, VoiceCastError) as exc:
                failures.append(f"{preference.model_id}: {exc}")
        detail = failures[-1] if failures else "no configured audio model was available"
        raise VoiceCastError(f"Audio analysis could not produce a voice-cast proposal ({detail}).")
