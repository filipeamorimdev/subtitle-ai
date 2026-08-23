"""Duration-aware dialogue adaptation. Called only when TTS does not fit."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.core.logging import get_logger

logger = get_logger("dubbing.translation")

SYSTEM_PROMPT = """You adapt dubbed dialogue so it can be spoken naturally in the available time.

Preserve meaning, intent and natural speech. Adapt wording when necessary so the
dialogue can naturally fit within approximately the available duration. Do not
mechanically shorten unless necessary. Do not add commentary. Return only the
adapted spoken line.
"""

AdaptFn = Callable[..., Awaitable[str]]


class DubTranslationService:
    def __init__(self, adapt: AdaptFn | None = None) -> None:
        self._adapt = adapt
        self.adapt_count = 0

    async def adapt_if_needed(
        self,
        text: str,
        *,
        target_language: str,
        available_duration: float,
        speaker_id: str | None = None,
    ) -> str:
        if self._adapt is None:
            logger.info("dub_adapt_skipped reason=no_provider")
            return text
        self.adapt_count += 1
        logger.info(
            "dub_adapt_start duration=%.2f language=%s speaker=%s chars=%s",
            available_duration,
            target_language,
            speaker_id,
            len(text),
        )
        adapted = await self._adapt(
            text=text,
            target_language=target_language,
            available_duration=available_duration,
            speaker_id=speaker_id,
        )
        result = (adapted or "").strip() or text
        logger.info("dub_adapt_done chars=%s->%s", len(text), len(result))
        return result


def build_adapt_prompt(
    *,
    text: str,
    target_language: str,
    available_duration: float,
    speaker_id: str | None,
) -> tuple[str, str]:
    speaker = f"Speaker: {speaker_id}\n" if speaker_id else ""
    user = (
        f"Target language: {target_language}\n"
        f"Available duration: {available_duration:.2f} seconds\n"
        f"{speaker}"
        f"Source text:\n{text}\n"
    )
    return SYSTEM_PROMPT, user
