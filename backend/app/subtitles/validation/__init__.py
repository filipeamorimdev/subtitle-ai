"""Subtitle validation."""

from __future__ import annotations

from dataclasses import dataclass

from app.subtitles.markup import extract_tag_sequence
from app.subtitles.models import SubtitleDocument


@dataclass
class ValidationIssue:
    code: str
    message: str


@dataclass
class ValidationResult:
    ok: bool
    issues: list[ValidationIssue]

    @property
    def error_message(self) -> str:
        return "; ".join(f"{i.code}: {i.message}" for i in self.issues)


def validate_source(document: SubtitleDocument) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if not document.blocks:
        issues.append(ValidationIssue("empty", "Source subtitle has no blocks"))
    seen: set[int] = set()
    for block in document.blocks:
        if block.index in seen:
            issues.append(ValidationIssue("duplicate_id", f"Duplicate block id {block.index}"))
        seen.add(block.index)
        if not block.start or not block.end:
            issues.append(ValidationIssue("timestamp", f"Missing timestamp on block {block.index}"))
        if block.text.strip() == "":
            issues.append(ValidationIssue("empty_text", f"Empty text on block {block.index}"))
    return ValidationResult(ok=not issues, issues=issues)


def validate_translation(
    source: SubtitleDocument,
    translated: SubtitleDocument,
    *,
    check_markup: bool = True,
) -> ValidationResult:
    issues: list[ValidationIssue] = []

    if len(source.blocks) != len(translated.blocks):
        issues.append(
            ValidationIssue(
                "block_count",
                f"Block count changed from {len(source.blocks)} to {len(translated.blocks)}",
            )
        )
        return ValidationResult(ok=False, issues=issues)

    for src, dst in zip(source.blocks, translated.blocks, strict=True):
        if src.index != dst.index:
            issues.append(
                ValidationIssue(
                    "block_id",
                    f"Block ID changed from {src.index} to {dst.index}",
                )
            )
        if src.start != dst.start or src.end != dst.end:
            issues.append(
                ValidationIssue(
                    "timestamp",
                    f"Timestamps changed on block {src.index}",
                )
            )
        if src.text.strip() and not dst.text.strip():
            issues.append(
                ValidationIssue(
                    "empty_translation",
                    f"Translated text empty for block {src.index}",
                )
            )
        if check_markup:
            src_tags = extract_tag_sequence(src.original_text or src.text)
            dst_tags = extract_tag_sequence(dst.text)
            if src_tags != dst_tags:
                issues.append(
                    ValidationIssue(
                        "markup",
                        f"Markup mismatch on block {src.index}: {src_tags} vs {dst_tags}",
                    )
                )

    return ValidationResult(ok=not issues, issues=issues)


def validate_batch_mapping(
    expected_ids: list[int],
    received: dict[int, str],
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    expected_set = set(expected_ids)
    received_set = set(received.keys())
    missing = expected_set - received_set
    extra = received_set - expected_set
    if missing:
        issues.append(ValidationIssue("missing_blocks", f"Missing block IDs: {sorted(missing)}"))
    if extra:
        issues.append(ValidationIssue("extra_blocks", f"Unexpected block IDs: {sorted(extra)}"))
    for block_id in expected_ids:
        text = received.get(block_id)
        if text is not None and text.strip() == "":
            issues.append(ValidationIssue("empty_translation", f"Empty translation for [{block_id:03d}]"))
    return ValidationResult(ok=not issues, issues=issues)
