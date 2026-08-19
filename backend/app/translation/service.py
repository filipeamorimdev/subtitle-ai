"""Provider-agnostic subtitle translation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ai.errors import AIProviderError
from app.ai.models import CAPABILITY_BATCH
from app.core.logging import get_logger
from app.subtitles.markup import protect_markup, restore_markup
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.validation import validate_batch_mapping, validate_translation
from app.translation.prompts import (
    MISSING_BLOCKS_PROMPT_EXTRA,
    build_missing_repair_user_message,
    build_system_prompt,
    build_translate_user_message,
    parse_batch_response,
)
from app.translation.openrouter.client import BatchChatRequest, batch_base_model, is_batch_model

logger = get_logger("translation")

MAX_CORRECTION_ATTEMPTS = 2
DEFAULT_BATCH_SIZE = 25


class _ChatLike(Protocol):
    """Minimal protocol: AIProvider or RecordingAIProvider."""

    async def chat_completion(self, *args: Any, **kwargs: Any) -> Any: ...

    def supports(self, capability: str) -> bool: ...


@dataclass
class TranslationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, result: Any) -> None:
        self.input_tokens += getattr(result, "input_tokens", None) or 0
        self.output_tokens += getattr(result, "output_tokens", None) or 0
        self.total_tokens += getattr(result, "total_tokens", None) or 0


@dataclass
class TranslationOutcome:
    document: SubtitleDocument
    usage: TranslationUsage
    model: str
    warnings: list[str] = field(default_factory=list)
    repair_used: bool = False
    provider_id: str | None = None


@dataclass
class TranslationCheckpoint:
    translated_by_id: dict[int, SubtitleBlock] = field(default_factory=dict)
    usage: TranslationUsage = field(default_factory=TranslationUsage)
    completed_batches: int = 0
    warnings: list[str] = field(default_factory=list)
    repair_used: bool = False


class RetryableTranslationError(AIProviderError):
    """Technical failure after some batches succeeded — caller should try the next model."""

    def __init__(
        self,
        message: str,
        *,
        checkpoint: TranslationCheckpoint,
        status_code: int | None = None,
        category: str = "provider_error",
        **kwargs: Any,
    ):
        kwargs.setdefault("is_retryable", True)
        kwargs.setdefault("status_code", status_code)
        kwargs.setdefault("category", category)
        super().__init__(message, **kwargs)
        self.checkpoint = checkpoint


def _validation_error(message: str) -> AIProviderError:
    return AIProviderError(message, category="validation_error", is_retryable=False)


def _content(result: Any) -> str:
    return str(getattr(result, "content", "") or "")


def _model_supports_batch(provider: _ChatLike, model_id: str) -> bool:
    supports_fn = getattr(provider, "supports", None)
    if callable(supports_fn):
        try:
            if not supports_fn(CAPABILITY_BATCH):
                return False
        except Exception:  # noqa: BLE001
            pass
    # OpenRouter encodes batch in the model slug; adapters without supports()
    # still honor :batch for behavioral compatibility with v0.2.
    return is_batch_model(model_id)


class TranslationService:
    """Translates subtitle documents via a generic AIProvider."""

    def __init__(self, provider: _ChatLike, *, temperature: float = 0) -> None:
        self.provider = provider
        # Alias kept for call sites / tests that still say `.client`.
        self.client = provider
        self.temperature = temperature

    async def translate_document(
        self,
        document: SubtitleDocument,
        *,
        model: str,
        target_language_code: str,
        target_language_name: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        progress_callback=None,
        checkpoint: TranslationCheckpoint | None = None,
        provider_id: str | None = None,
        locale_note: str = "",
        glossary_block: str = "",
    ) -> TranslationOutcome:
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")

        system_prompt = build_system_prompt(
            target_language_code,
            target_language_name,
            locale_note=locale_note,
            glossary_block=glossary_block,
        )
        state = checkpoint or TranslationCheckpoint()
        usage = state.usage
        translated_by_id: dict[int, SubtitleBlock] = dict(state.translated_by_id)
        batches = [
            document.blocks[i : i + batch_size]
            for i in range(0, len(document.blocks), batch_size)
        ]
        total_batches = len(batches)
        warnings: list[str] = list(state.warnings)
        repair_used = state.repair_used
        start_index = state.completed_batches
        completed_batches = start_index
        sync_model = batch_base_model(model)
        pid = provider_id or getattr(self.provider, "provider_id", None)

        def _checkpoint(completed: int) -> TranslationCheckpoint:
            return TranslationCheckpoint(
                translated_by_id=dict(translated_by_id),
                usage=usage,
                completed_batches=completed,
                warnings=list(warnings),
                repair_used=repair_used,
            )

        try:
            remaining = batches[start_index:]
            if _model_supports_batch(self.provider, model) and remaining:
                await self._translate_document_via_batch(
                    batches=remaining,
                    model=model,
                    sync_model=sync_model,
                    system_prompt=system_prompt,
                    usage=usage,
                    translated_by_id=translated_by_id,
                    progress_callback=progress_callback,
                    batch_offset=start_index,
                    total_batches=total_batches,
                )
                completed_batches = total_batches
            else:
                for local_index, batch in enumerate(remaining, start=1):
                    batch_index = start_index + local_index
                    protected_payload: list[tuple[int, str, list[str]]] = []
                    for block in batch:
                        protection = protect_markup(block.text)
                        protected_payload.append(
                            (block.index, protection.protected_text, protection.tags)
                        )

                    mapping, repaired = await self._translate_batch(
                        model=sync_model,
                        system_prompt=system_prompt,
                        protected_payload=protected_payload,
                        usage=usage,
                    )
                    if repaired:
                        repair_used = True
                    self._apply_mapping(batch, protected_payload, mapping, translated_by_id)
                    completed_batches = batch_index

                    if progress_callback:
                        await progress_callback(batch_index, total_batches)
        except AIProviderError as exc:
            if getattr(exc, "is_retryable", False) or getattr(exc, "retryable", False):
                if not isinstance(exc, RetryableTranslationError):
                    raise RetryableTranslationError(
                        str(exc),
                        checkpoint=_checkpoint(completed_batches),
                        status_code=getattr(exc, "status_code", None),
                        category=getattr(exc, "category", "provider_error"),
                        original_error=exc,
                        provider_id=pid,
                        model_id=model,
                    ) from exc
            raise
        except Exception as exc:
            # Legacy OpenRouterClient / FakeClient errors.
            from app.translation.openrouter.client import OpenRouterError

            if isinstance(exc, OpenRouterError) and getattr(exc, "retryable", False):
                raise RetryableTranslationError(
                    str(exc),
                    checkpoint=_checkpoint(completed_batches),
                    status_code=getattr(exc, "status_code", None),
                    category="provider_error",
                    original_error=exc,
                    provider_id=pid,
                    model_id=model,
                ) from exc
            raise

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
                raise _validation_error(
                    f"Translation response failed validation. {validation.error_message}"
                )

            logger.warning(
                "Hard validation failed for blocks %s; splitting and retrying individually",
                hard_ids,
            )
            repair_used = True
            source_by_id = {block.index: block for block in document.blocks}
            for block_id in hard_ids:
                source_block = source_by_id[block_id]
                protection = protect_markup(source_block.text)
                mapping, _repaired = await self._translate_batch(
                    model=sync_model,
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
                raise _validation_error(
                    "Translation response failed validation. "
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
        final_validation = validate_translation(document, result_doc, check_markup=True)
        for issue in final_validation.soft_issues:
            message = f"{issue.code}: {issue.message}"
            if message not in warnings:
                warnings.append(message)
        if not final_validation.hard_ok:
            raise _validation_error(
                "Translation response failed validation. "
                + "; ".join(f"{i.code}: {i.message}" for i in final_validation.hard_issues)
            )

        from app.subtitles.reading import analyze_document, overcrowded_blocks, reading_repair_prompt_extra

        crowded = overcrowded_blocks(result_doc)
        if crowded:
            repair_used = True
            protected_payload: list[tuple[int, str, list[str]]] = []
            for block in crowded:
                protection = protect_markup(block.text)
                protected_payload.append(
                    (block.index, protection.protected_text, protection.tags)
                )
            mapping, _ = await self._translate_batch(
                model=sync_model,
                system_prompt=system_prompt + reading_repair_prompt_extra(),
                protected_payload=protected_payload,
                usage=usage,
            )
            self._apply_mapping(crowded, protected_payload, mapping, translated_by_id)
            result_doc = SubtitleDocument(
                format=document.format,
                encoding=document.encoding,
                blocks=[translated_by_id[b.index] for b in document.blocks],
            )
            leftover = analyze_document(result_doc)
            if leftover:
                warnings.append(
                    f"reading_speed: {len({i.block_index for i in leftover})} cue(s) still over reading limits"
                )

        return TranslationOutcome(
            document=result_doc,
            usage=usage,
            model=model,
            warnings=warnings,
            repair_used=repair_used,
            provider_id=pid,
        )

    async def _chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> Any:
        """Call provider.chat_completion with both model_id and legacy model= support."""
        kwargs.setdefault("temperature", self.temperature)
        try:
            return await self.provider.chat_completion(
                model_id=model, messages=messages, **kwargs
            )
        except TypeError:
            # Legacy OpenRouterClient signature uses model=
            return await self.provider.chat_completion(model=model, messages=messages, **kwargs)

    async def _translate_document_via_batch(
        self,
        *,
        batches: list[list[SubtitleBlock]],
        model: str,
        sync_model: str,
        system_prompt: str,
        usage: TranslationUsage,
        translated_by_id: dict[int, SubtitleBlock],
        progress_callback,
        batch_offset: int = 0,
        total_batches: int | None = None,
    ) -> None:
        total = total_batches if total_batches is not None else len(batches)
        chunk_payloads: list[list[tuple[int, str, list[str]]]] = []
        requests: list[BatchChatRequest] = []

        for batch_index, batch in enumerate(batches, start=1):
            protected_payload: list[tuple[int, str, list[str]]] = []
            for block in batch:
                protection = protect_markup(block.text)
                protected_payload.append((block.index, protection.protected_text, protection.tags))
            chunk_payloads.append(protected_payload)
            source_blocks = [(block_id, text) for block_id, text, _tags in protected_payload]
            requests.append(
                BatchChatRequest(
                    custom_id=f"chunk-{batch_offset + batch_index}",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": build_translate_user_message(source_blocks),
                        },
                    ],
                    temperature=self.temperature,
                )
            )

        async def on_batch_progress(completed: int, _total: int) -> None:
            if progress_callback:
                await progress_callback(min(batch_offset + completed, total), total)

        run_batch = getattr(self.provider, "run_chat_batch", None)
        if run_batch is None:
            raise AIProviderError(
                "Provider does not support batch completions",
                category="incompatible",
                is_retryable=False,
            )
        try:
            results = await run_batch(
                model_id=model,
                requests=requests,
                progress_callback=on_batch_progress,
            )
        except TypeError:
            results = await run_batch(
                model=model,
                requests=requests,
                progress_callback=on_batch_progress,
            )

        for batch_index, (batch, protected_payload) in enumerate(
            zip(batches, chunk_payloads, strict=True),
            start=1,
        ):
            custom_id = f"chunk-{batch_offset + batch_index}"
            initial = results[custom_id]
            mapping, _repaired = await self._translate_batch(
                model=sync_model,
                system_prompt=system_prompt,
                protected_payload=protected_payload,
                usage=usage,
                initial_result=initial,
            )
            self._apply_mapping(batch, protected_payload, mapping, translated_by_id)

        if progress_callback:
            await progress_callback(total, total)

    @staticmethod
    def _apply_mapping(
        batch: list[SubtitleBlock],
        protected_payload: list[tuple[int, str, list[str]]],
        mapping: dict[int, str],
        translated_by_id: dict[int, SubtitleBlock],
    ) -> None:
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

    async def _translate_batch(
        self,
        *,
        model: str,
        system_prompt: str,
        protected_payload: list[tuple[int, str, list[str]]],
        usage: TranslationUsage,
        initial_result: Any | None = None,
    ) -> tuple[dict[int, str], bool]:
        expected_ids = [item[0] for item in protected_payload]
        expected_set = set(expected_ids)
        by_id = {item[0]: (item[1], item[2]) for item in protected_payload}
        source_blocks = [(block_id, by_id[block_id][0]) for block_id in expected_ids]
        repaired = False

        if initial_result is not None:
            result = initial_result
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": build_translate_user_message(source_blocks),
                },
            ]
            result = await self._chat(model=model, messages=messages)

        usage.add(result)
        mapping = self._filter_mapping(parse_batch_response(_content(result)), expected_set)
        validation = validate_batch_mapping(expected_ids, mapping)
        if validation.ok:
            return mapping, repaired

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
            repaired = True
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
            retry = await self._chat(model=model, messages=correction_messages)
            usage.add(retry)
            repaired_map = self._filter_mapping(
                parse_batch_response(_content(retry)), set(incomplete_ids)
            )
            mapping = self._merge_mapping(mapping, repaired_map, expected_set)
            validation = validate_batch_mapping(expected_ids, mapping)
            if validation.ok:
                return mapping, repaired

        if len(protected_payload) > 1:
            mid = len(protected_payload) // 2
            logger.warning(
                "Batch validation failed after correction; shrinking %s blocks into %s + %s",
                len(protected_payload),
                mid,
                len(protected_payload) - mid,
            )
            repaired = True
            left, left_repaired = await self._translate_batch(
                model=model,
                system_prompt=system_prompt,
                protected_payload=protected_payload[:mid],
                usage=usage,
            )
            right, right_repaired = await self._translate_batch(
                model=model,
                system_prompt=system_prompt,
                protected_payload=protected_payload[mid:],
                usage=usage,
            )
            return {**left, **right}, repaired or left_repaired or right_repaired

        raise _validation_error(
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


# Transitional alias for legacy imports.
OpenRouterTranslationService = TranslationService
