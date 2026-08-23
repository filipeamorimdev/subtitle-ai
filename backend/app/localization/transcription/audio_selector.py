"""Select the primary dialogue audio stream from ffprobe metadata."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.media.process_runner import ProcessError, run_process_checked
from app.subtitles.filenames import language_matches, normalize_language_code

logger = get_logger("audio_selector")

COMMENTARY_TOKENS = (
    "commentary",
    "director",
    "comment",
)
DESCRIPTION_TOKENS = (
    "audio description",
    "audiodescription",
    "audio_description",
    "described",
    "descriptive",
    "visually impaired",
    "ad track",
)


@dataclass(frozen=True)
class AudioStream:
    stream_index: int
    language: str | None
    channels: int
    title: str | None = None
    default: bool = False
    comment: bool = False
    visual_impaired: bool = False
    codec: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredAudioStream:
    stream: AudioStream
    score: float
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream_index": self.stream.stream_index,
            "language": self.stream.language,
            "channels": self.stream.channels,
            "title": self.stream.title,
            "score": self.score,
            "reasons": list(self.reasons),
            "default": self.stream.default,
            "comment": self.stream.comment,
        }


@dataclass
class AudioSelection:
    selected: ScoredAudioStream | None
    candidates: list[ScoredAudioStream]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        if self.selected is None:
            return {"selected": None, "reason": self.reason, "candidates": []}
        stream = self.selected.stream
        return {
            "stream_index": stream.stream_index,
            "language": stream.language,
            "channels": stream.channels,
            "score": self.selected.score,
            "reason": self.reason,
            "title": stream.title,
            "candidates": [item.to_dict() for item in self.candidates],
        }


def _title_blob(stream: AudioStream) -> str:
    return " ".join(part for part in (stream.title, str(stream.metadata)) if part).lower()


def _has_token(blob: str, tokens: tuple[str, ...]) -> bool:
    return any(token in blob for token in tokens)


def score_audio_stream(
    stream: AudioStream,
    *,
    preferred_languages: list[str],
    allow_commentary: bool = False,
    allow_description: bool = False,
) -> ScoredAudioStream:
    reasons: list[str] = []
    score = 40.0
    blob = _title_blob(stream)

    if stream.language and language_matches(stream.language, preferred_languages):
        score += 50.0
        reasons.append("Preferred language")
    elif stream.language:
        score += 8.0
        reasons.append("Labeled language")
    else:
        score -= 12.0
        reasons.append("Unknown language")

    if stream.default:
        score += 20.0
        reasons.append("Default disposition")

    if stream.channels >= 6:
        score += 10.0
        reasons.append("Multi-channel")
    elif stream.channels >= 2:
        score += 6.0
        reasons.append("Stereo")
    elif stream.channels == 1:
        score += 2.0

    commentary = stream.comment or _has_token(blob, COMMENTARY_TOKENS)
    description = stream.visual_impaired or _has_token(blob, DESCRIPTION_TOKENS)
    if commentary and not allow_commentary:
        score -= 80.0
        reasons.append("Commentary penalty")
    if description and not allow_description:
        score -= 80.0
        reasons.append("Audio description penalty")
    if not commentary and not description:
        reasons.append("Non-commentary")

    return ScoredAudioStream(stream=stream, score=round(score, 2), reasons=tuple(reasons))


def select_audio_stream(
    streams: list[AudioStream],
    *,
    preferred_languages: list[str],
    allow_commentary: bool = False,
    allow_description: bool = False,
) -> AudioSelection:
    if not streams:
        return AudioSelection(selected=None, candidates=[], reason="No audio streams")
    scored = [
        score_audio_stream(
            stream,
            preferred_languages=preferred_languages,
            allow_commentary=allow_commentary,
            allow_description=allow_description,
        )
        for stream in streams
    ]
    scored.sort(key=lambda item: (-item.score, item.stream.stream_index))
    winner = scored[0]
    reason = "; ".join(winner.reasons) or "Highest scoring audio stream"
    logger.info(
        "audio_selected stream_index=%s language=%s channels=%s score=%s reason=%s",
        winner.stream.stream_index,
        winner.stream.language,
        winner.stream.channels,
        winner.score,
        reason,
    )
    for item in scored:
        logger.info(
            "audio_candidate stream_index=%s language=%s channels=%s title=%s score=%s reasons=%s",
            item.stream.stream_index,
            item.stream.language,
            item.stream.channels,
            item.stream.title,
            item.score,
            "; ".join(item.reasons),
        )
    return AudioSelection(selected=winner, candidates=scored, reason=reason)


def streams_from_ffprobe(payload: dict[str, Any]) -> list[AudioStream]:
    raw = payload.get("streams") or []
    streams: list[AudioStream] = []
    if not isinstance(raw, list):
        return streams
    for item in raw:
        if not isinstance(item, dict):
            continue
        codec_type = str(item.get("codec_type") or "audio").lower()
        if codec_type not in {"audio", ""}:
            continue
        tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
        disposition = item.get("disposition") if isinstance(item.get("disposition"), dict) else {}
        index = item.get("index")
        if index is None:
            continue
        try:
            stream_index = int(index)
        except (TypeError, ValueError):
            continue
        try:
            channels = int(item.get("channels") or 0)
        except (TypeError, ValueError):
            channels = 0
        language = normalize_language_code(tags.get("language") if tags else None)
        title = tags.get("title") if tags else None

        def _flag(value: object) -> bool:
            if isinstance(value, bool):
                return value
            try:
                return int(value or 0) != 0
            except (TypeError, ValueError):
                return str(value).strip().lower() in {"1", "true", "yes"}

        streams.append(
            AudioStream(
                stream_index=stream_index,
                language=language,
                channels=channels,
                title=str(title) if title else None,
                default=_flag(disposition.get("default")),
                comment=_flag(disposition.get("comment")),
                visual_impaired=_flag(
                    disposition.get("visual_impaired") or disposition.get("descriptions")
                ),
                codec=item.get("codec_name"),
                metadata={"tags": tags, "disposition": disposition},
            )
        )
    return streams


class AudioTrackSelector:
    async def probe(self, media_path: str | Path, *, timeout_s: float = 60.0) -> list[AudioStream]:
        media = Path(media_path)
        result = await run_process_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index,codec_type,codec_name,channels:stream_tags=language,title:stream_disposition=default,comment,visual_impaired,descriptions",
                "-of",
                "json",
                str(media),
            ],
            timeout_s=timeout_s,
        )
        try:
            payload = json.loads(result.stdout_text or "{}")
        except json.JSONDecodeError as exc:
            raise ProcessError(
                f"ffprobe returned invalid JSON for {media.name}",
                outcome=result.outcome,
            ) from exc
        if not isinstance(payload, dict):
            return []
        return streams_from_ffprobe(payload)

    async def select(
        self,
        media_path: str | Path,
        *,
        preferred_languages: list[str],
        allow_commentary: bool = False,
        allow_description: bool = False,
    ) -> AudioSelection:
        streams = await self.probe(media_path)
        return select_audio_stream(
            streams,
            preferred_languages=preferred_languages,
            allow_commentary=allow_commentary,
            allow_description=allow_description,
        )
