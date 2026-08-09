"""OpenRouter-backed translation service."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.subtitles.markup import protect_markup, restore_markup
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.validation import validate_batch_mapping, validate_translation
from app.translation.openrouter.client import ChatResult, OpenRouterClient, OpenRouterError
from app.translation.openrouter.prompts import (
    MISSING_BLOCKS_PROMPT_EXTRA,
    build_missing_repair_user_message,
    build_system_prompt,
    build_translate_user_message,
    parse_batch_response,
)

logger = get_logger("translation")

# Bounded recovery: a few targeted repairs, then split the batch and retry.
MAX_CORRECTION_ATTEMPTS = 2
DEFAULT_BATCH_SIZE = 25


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
    warnings: list[str] = field(default_factory=list)


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
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress_callback=None,
        glossary_terms=None,
    ) -> TranslationOutcome:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        system_prompt = build_system_prompt(
            target_language_code,
            target_language_name,
            glossary_terms=glossary_terms,
        )
        usage = TranslationUsage()
        translated_by_id: dict[int, SubtitleBlock] = {}
        batches = [
            document.blocks[i : i + batch_size]
            for i in range(0, len(document.blocks), batch_size)
        ]
        total_batches = len(batches)
        warnings: list[str] = []

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
                translated_by_id[block.index] = SubtitleBlock(
                    index=block.index,
                    start=block.start,
                    end=block.end,
                    text=restored,
                    original_text=block.original_text or block.text,
                )

            if progress_callback:
                await progress_callback(batch_index, total_batches)

        # Final validation: markup mismatches are warnings; hard issues re-translate
        # the offending blocks one-by-one (split + retry).
        for _round in range(3):
            result_doc = SubtitleDocument(
                format=document.format,
                encoding=document.encoding,
                blocks=[translated_by_id[b.index] for b in document.blocks],
            )
            validation = validate_translation(document, result_doc, check_markup=True)
            for issue in validation.soft_issues:
                message = f"{issue.code}: {issue.message}"
                if message not in warnings:
                    warnings.append(message)
                    logger.warning("Translation soft validation: %s", message)

            hard_ids = validation.hard_block_ids()
            if validation.hard_ok:
                break
            if not hard_ids:
                raise OpenRouterError(
                    f"Translation response failed validation. {validation.error_message}"
                )

            logger.warning(
                "Hard validation failed for blocks %s; splitting and retrying individually",
                hard_ids,
            )
            source_by_id = {block.index: block for block in document.blocks}
            for block_id in hard_ids:
                source_block = source_by_id[block_id]
                protection = protect_markup(source_block.text)
                mapping = await self._translate_batch(
                    model=model,
                    system_prompt=system_prompt,
                    protected_payload=[
                        (source_block.index, protection.protected_text, protection.tags)
                    ],
                    usage=usage,
                )
                restored = restore_markup(mapping[block_id], protection.tags)
                translated_by_id[block_id] = SubtitleBlock(
                    index=source_block.index,
                    start=source_block.start,
                    end=source_block.end,
                    text=restored,
                    original_text=source_block.original_text or source_block.text,
                )
        else:
            result_doc = SubtitleDocument(
                format=document.format,
                encoding=document.encoding,
                blocks=[translated_by_id[b.index] for b in document.blocks],
            )
            validation = validate_translation(document, result_doc, check_markup=True)
            if not validation.hard_ok:
                raise OpenRouterError(
                    f"Translation response failed validation. "
                    + "; ".join(f"{i.code}: {i.message}" for i in validation.hard_issues)
                )
            for issue in validation.soft_issues:
                message = f"{issue.code}: {issue.message}"
                if message not in warnings:
                    warnings.append(message)

        result_doc = SubtitleDocument(
            format=document.format,
            encoding=document.encoding,
            blocks=[translated_by_id[b.index] for b in document.blocks],
        )
        # Re-collect soft markup warnings from the final document.
        final_validation = validate_translation(document, result_doc, check_markup=True)
        for issue in final_validation.soft_issues:
            message = f"{issue.code}: {issue.message}"
            if message not in warnings:
                warnings.append(message)
        if not final_validation.hard_ok:
            raise OpenRouterError(
                f"Translation response failed validation. "
                + "; ".join(f"{i.code}: {i.message}" for i in final_validation.hard_issues)
            )

        return TranslationOutcome(
            document=result_doc,
            usage=usage,
            model=model,
            warnings=warnings,
        )

    async def _translate_batch(
        self,
        *,
        model: str,
        system_prompt: str,
        protected_payload: list[tuple[int, str, list[str]]],
        usage: TranslationUsage,
    ) -> dict[int, str]:
        expected_ids = [item[0] for item in protected_payload]
        expected_set = set(expected_ids)
        by_id = {item[0]: (item[1], item[2]) for item in protected_payload}
        source_blocks = [(block_id, by_id[block_id][0]) for block_id in expected_ids]

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_translate_user_message(source_blocks),
            },
        ]

        result = await self.client.chat_completion(model=model, messages=messages)
        usage.add(result)
        mapping = self._filter_mapping(parse_batch_response(result.content), expected_set)
        validation = validate_batch_mapping(expected_ids, mapping)
        if validation.ok:
            return mapping

        for attempt in range(1, MAX_CORRECTION_ATTEMPTS + 1):
            incomplete_ids = self._incomplete_ids(expected_ids, mapping)
            if not incomplete_ids:
                break

            logger.warning(
                "Batch validation failed; targeted repair attempt %s/%s for ids=%s",
                attempt,
                MAX_CORRECTION_ATTEMPTS,
                incomplete_ids,
            )
            repair_blocks = [(block_id, by_id[block_id][0]) for block_id in incomplete_ids]
            correction_messages = [
                {
                    "role": "system",
                    "content": system_prompt + MISSING_BLOCKS_PROMPT_EXTRA,
                },
                {
                    "role": "user",
                    "content": build_missing_repair_user_message(
                        repair_blocks,
                        missing_ids=incomplete_ids,
                    ),
                },
            ]
            retry = await self.client.chat_completion(
                model=model,
                messages=correction_messages,
            )
            usage.add(retry)
            repaired = self._filter_mapping(parse_batch_response(retry.content), set(incomplete_ids))
            mapping = self._merge_mapping(mapping, repaired, expected_set)
            validation = validate_batch_mapping(expected_ids, mapping)
            if validation.ok:
                return mapping

        if len(protected_payload) > 1:
            mid = len(protected_payload) // 2
            logger.warning(
                "Batch validation failed after correction; shrinking %s blocks into %s + %s",
                len(protected_payload),
                mid,
                len(protected_payload) - mid,
            )
            left = await self._translate_batch(
                model=model,
                system_prompt=system_prompt,
                protected_payload=protected_payload[:mid],
                usage=usage,
            )
            right = await self._translate_batch(
                model=model,
                system_prompt=system_prompt,
                protected_payload=protected_payload[mid:],
                usage=usage,
            )
            return {**left, **right}

        raise OpenRouterError(
            "Translation response failed validation. " + validation.error_message
        )

    @staticmethod
    def _filter_mapping(mapping: dict[int, str], expected_set: set[int]) -> dict[int, str]:
        return {block_id: text for block_id, text in mapping.items() if block_id in expected_set}

    @staticmethod
    def _incomplete_ids(expected_ids: list[int], mapping: dict[int, str]) -> list[int]:
        incomplete: list[int] = []
        for block_id in expected_ids:
            text = mapping.get(block_id)
            if text is None or text.strip() == "":
                incomplete.append(block_id)
        return incomplete

    @staticmethod
    def _merge_mapping(
        base: dict[int, str],
        repaired: dict[int, str],
        expected_set: set[int],
    ) -> dict[int, str]:
        merged = dict(base)
        for block_id, text in repaired.items():
            if block_id not in expected_set:
                continue
            if text.strip() == "":
                continue
            merged[block_id] = text
        return merged
