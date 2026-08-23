"""OpenAI Whisper API ASR provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import get_logger
from app.localization.transcription.models import Transcript, TranscriptSegment, TranscriptWord
from app.subtitles.filenames import normalize_language_code

logger = get_logger("asr.openai")

OPENAI_WHISPER_MODEL = "whisper-1"
OPENAI_REQUEST_TIMEOUT = 600.0
LOW_CONFIDENCE = 0.5

CancelCheck = Callable[[], bool]
ProgressCb = Callable[[float, float], Awaitable[None] | None]


class OpenAITranscribeError(Exception):
    pass


def _segments_from_openai(payload: dict[str, Any], *, offset: float = 0.0) -> list[TranscriptSegment]:
    raw = payload.get("segments") or []
    segments: list[TranscriptSegment] = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            words: list[TranscriptWord] = []
            for raw_word in item.get("words") or []:
                if not isinstance(raw_word, dict):
                    continue
                text = str(raw_word.get("word") or raw_word.get("text") or "").strip()
                if not text:
                    continue
                words.append(
                    TranscriptWord(
                        start=float(raw_word.get("start") or 0.0) + offset,
                        end=float(raw_word.get("end") or 0.0) + offset,
                        text=text,
                        probability=None,
                    )
                )
            segments.append(
                TranscriptSegment(
                    start=float(item.get("start") or 0.0) + offset,
                    end=float(item.get("end") or 0.0) + offset,
                    text=str(item.get("text") or ""),
                    no_speech_prob=float(item.get("no_speech_prob") or 0.0),
                    words=tuple(words),
                )
            )
        return segments
    text = str(payload.get("text") or "").strip()
    if text:
        segments.append(TranscriptSegment(start=offset, end=offset + 1.0, text=text))
    return segments


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()

    async def _transcribe_file(self, client: httpx.AsyncClient, path: Path, language: str | None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "model": OPENAI_WHISPER_MODEL,
            "response_format": "verbose_json",
            "timestamp_granularities[]": "word",
        }
        if language:
            data["language"] = language
        suffix = path.suffix.lower()
        mime = "audio/mpeg" if suffix == ".mp3" else "audio/wav"
        with path.open("rb") as handle:
            response = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (path.name, handle, mime)},
                data=data,
            )
        if response.status_code >= 400:
            detail = response.text[:400]
            raise OpenAITranscribeError(f"OpenAI Whisper API failed ({response.status_code}): {detail}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise OpenAITranscribeError("OpenAI Whisper API returned an unexpected payload.")
        return payload

    async def transcribe(
        self,
        audio_path: Path,
        language: str | None = None,
        word_timestamps: bool = True,
        *,
        duration: float | None = None,
        is_cancelled: CancelCheck | None = None,
        on_progress: ProgressCb | None = None,
    ) -> Transcript:
        path = Path(audio_path)
        if not path.is_file():
            raise OpenAITranscribeError("Extracted audio is not readable.")
        if not self.api_key:
            raise OpenAITranscribeError("OpenAI API key is not configured.")
        if is_cancelled and is_cancelled():
            raise OpenAITranscribeError("Transcription cancelled.")

        timeout = httpx.Timeout(OPENAI_REQUEST_TIMEOUT)
        async with httpx.AsyncClient(timeout=timeout) as client:
            payload = await self._transcribe_file(client, path, language)

        detected = normalize_language_code(payload.get("language"))
        warnings: list[str] = []
        if detected is None:
            warnings.append("OpenAI transcription did not return a language code.")
        confidence = None
        raw_conf = payload.get("language_probability") or payload.get("language_confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None and confidence < LOW_CONFIDENCE:
            warnings.append(
                f"Low language confidence ({confidence:.2f}) for detected language {detected}."
            )
            logger.warning(
                "transcription_language_low_confidence detected=%s confidence=%s requested=%s",
                detected,
                confidence,
                language,
            )
        segments = _segments_from_openai(payload)
        if on_progress:
            done = duration or (segments[-1].end if segments else 0.0)
            result = on_progress(done, done or 1.0)
            if hasattr(result, "__await__"):
                await result  # type: ignore[misc]
        return Transcript(
            language=detected,
            language_confidence=confidence,
            segments=tuple(segments),
            requested_language=language,
            provider=f"openai:{OPENAI_WHISPER_MODEL}",
            duration=duration,
            warnings=tuple(warnings),
            metadata={"word_timestamps": word_timestamps},
        )
