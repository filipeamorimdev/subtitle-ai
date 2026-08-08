"""OpenRouter-backed translation service."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.subtitles.markup import protect_markup, restore_markup
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.validation import validate_batch_mapping, validate_translation
from app.translation.openrouter.client import ChatResult, OpenRouterClient, OpenRouterError
from app.translation.openrouter.prompts import (
    CORRECTION_PROMPT_EXTRA,
    build_system_prompt,
    format_batch,
    parse_batch_response,
)

logger = get_logger("translation")


@dataclass
class TranslationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, result: ChatResult) -> None:
        self.input_tokens += result.input_tokens or 0
        self.output_tokens += result.output_tokens or 0
        self.total_tokens += result.total_tokens or 0


@dataclass
class TranslationOutcome:
    document: SubtitleDocument
    usage: TranslationUsage
    model: str


class OpenRouterTranslationService:
    def __init__(self, client: OpenRouterClient) -> None:
        self.client = client

    async def translate_document(
        self,
        document: SubtitleDocument,
        *,
        model: str,
        target_language_code: str,
        target_language_name: str,
        batch_size: int = 50,
        progress_callback=None,
    ) -> TranslationOutcome:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        system_prompt = build_system_prompt(target_language_code, target_language_name)
        usage = TranslationUsage()
        translated_blocks: list[SubtitleBlock] = []
        batches = [
            document.blocks[i : i + batch_size]
            for i in range(0, len(document.blocks), batch_size)
        ]
        total_batches = len(batches)

        for batch_index, batch in enumerate(batches, start=1):
            protected_payload: list[tuple[int, str, list[str]]] = []
            for block in batch:
                protection = protect_markup(block.text)
                protected_payload.append((block.index, protection.protected_text, protection.tags))

            mapping = await self._translate_batch(
                model=model,
                system_prompt=system_prompt,
                protected_payload=protected_payload,
                usage=usage,
            )

            for block, (block_id, _protected, tags) in zip(batch, protected_payload, strict=True):
                raw = mapping[block_id]
                restored = restore_markup(raw, tags)
                translated_blocks.append(
                    SubtitleBlock(
                        index=block.index,
                        start=block.start,
                        end=block.end,
                        text=restored,
                        original_text=block.original_text or block.text,
                    )
                )

            if progress_callback:
                await progress_callback(batch_index, total_batches)

        result_doc = SubtitleDocument(
            format=document.format,
            encoding=document.encoding,
            blocks=translated_blocks,
        )
        validation = validate_translation(document, result_doc, check_markup=True)
        if not validation.ok:
            raise OpenRouterError(
                f"Translation response failed validation. {validation.error_message}"
            )
        return TranslationOutcome(document=result_doc, usage=usage, model=model)

    async def _translate_batch(
        self,
        *,
        model: str,
        system_prompt: str,
        protected_payload: list[tuple[int, str, list[str]]],
        usage: TranslationUsage,
    ) -> dict[int, str]:
        expected_ids = [item[0] for item in protected_payload]
        user_content = format_batch([(i, text) for i, text, _ in protected_payload])

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Translate the following subtitle blocks. "
                    "Return the same IDs with translated text only.\n\n"
                    + user_content
                ),
            },
        ]

        result = await self.client.chat_completion(model=model, messages=messages)
        usage.add(result)
        mapping = parse_batch_response(result.content)
        validation = validate_batch_mapping(expected_ids, mapping)
        if validation.ok:
            return mapping

        logger.warning("Batch validation failed; attempting correction retry")
        correction_messages = [
            {"role": "system", "content": system_prompt + CORRECTION_PROMPT_EXTRA},
            {
                "role": "user",
                "content": (
                    "Correct this translation so every ID is present exactly once.\n\n"
                    "INPUT:\n"
                    + user_content
                    + "\nPREVIOUS OUTPUT:\n"
                    + result.content
                ),
            },
        ]
        retry = await self.client.chat_completion(model=model, messages=correction_messages)
        usage.add(retry)
        retry_mapping = parse_batch_response(retry.content)
        retry_validation = validate_batch_mapping(expected_ids, retry_mapping)
        if not retry_validation.ok:
            raise OpenRouterError(
                "Translation response failed validation. " + retry_validation.error_message
            )
        return retry_mapping
