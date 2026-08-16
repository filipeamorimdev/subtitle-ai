"""Translation prompts and batch formatting."""

from __future__ import annotations

import re
from typing import Protocol


class GlossaryTermLike(Protocol):
    source: str
    target: str
    term_type: str
    policy: str


SYSTEM_PROMPT = """You are a professional audiovisual subtitle translator.

Translate subtitle dialogue from the source language to the requested target language and locale.

Preserve:
- subtitle block IDs
- block ordering
- one translated result for every input block
- speaker intent
- tone
- profanity level
- names and proper nouns unless translation is clearly appropriate
- glossary terms exactly as specified
- placeholder tags like <TAG0>, <TAG1> exactly as given

Do not:
- add explanations
- add commentary
- merge blocks
- split blocks
- invent dialogue
- omit dialogue
- return markdown
- return timestamps
- modify IDs

The target locale is important. If the target is Portuguese (Portugal), use European Portuguese vocabulary, grammar, spelling, and natural conversational usage. Do not produce Brazilian Portuguese.

Return only the requested block mapping.
"""


def format_glossary_block(terms: list[GlossaryTermLike]) -> str:
    if not terms:
        return ""
    lines = [
        "",
        "GLOSSARY (must follow consistently):",
        "When a glossary source term appears, use the target form below.",
        "Do not invent alternate spellings for glossary terms.",
        "Respect morphology around terms (possessives, plurals) while keeping the glossary form.",
    ]
    for term in terms:
        lines.append(
            f'- "{term.source}" → "{term.target}" ({term.term_type}, {term.policy})'
        )
    lines.append("")
    return "\n".join(lines)

MISSING_BLOCKS_PROMPT_EXTRA = """
Some block IDs were missing or empty in a previous response. Translate ONLY the blocks provided below.

Return ONLY those IDs in this exact format:

[001]
translated line

Every requested ID must appear exactly once. Do not merge, split, omit, or renumber blocks. Do not add commentary. Do not return any other IDs.
"""

BLOCK_RE = re.compile(r"^\[(\d{3,})\]\s*$")


def build_system_prompt(
    target_language_code: str,
    target_language_name: str,
    *,
    glossary_terms: list[GlossaryTermLike] | None = None,
) -> str:
    locale_note = ""
    if target_language_code.lower() in {"pt-pt", "pt"} and "brazil" not in target_language_name.lower():
        locale_note = (
            "\nUse European Portuguese (PT-PT), not Brazilian Portuguese (PT-BR).\n"
        )
    glossary_note = format_glossary_block(glossary_terms or [])
    return (
        SYSTEM_PROMPT
        + f"\nTarget language code: {target_language_code}\n"
        + f"Target language name: {target_language_name}\n"
        + locale_note
        + glossary_note
    )


def format_batch(blocks: list[tuple[int, str]]) -> str:
    parts: list[str] = []
    for block_id, text in blocks:
        parts.append(f"[{block_id:03d}]")
        parts.append(text)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


def format_id_list(block_ids: list[int]) -> str:
    return ", ".join(f"[{block_id:03d}]" for block_id in block_ids)


def build_translate_user_message(blocks: list[tuple[int, str]]) -> str:
    count = len(blocks)
    ids = format_id_list([block_id for block_id, _ in blocks])
    return (
        f"Translate the following {count} subtitle blocks. "
        f"Return exactly {count} blocks with these IDs only: {ids}. "
        "Keep one-to-one ID mapping. Do not merge or split blocks. "
        "Return the same IDs with translated text only.\n\n"
        + format_batch(blocks)
    )


def build_missing_repair_user_message(
    blocks: list[tuple[int, str]],
    *,
    missing_ids: list[int],
) -> str:
    count = len(blocks)
    ids = format_id_list(missing_ids)
    return (
        f"Translate ONLY these {count} missing subtitle blocks. "
        f"Return exactly {count} blocks with these IDs only: {ids}. "
        "Do not return any other IDs.\n\n"
        + format_batch(blocks)
    )


def parse_batch_response(content: str) -> dict[int, str]:
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # Strip markdown fences if the model ignores instructions
    cleaned: list[str] = []
    for line in lines:
        if line.strip().startswith("```"):
            continue
        cleaned.append(line)

    result: dict[int, str] = {}
    current_id: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_id, current_lines
        if current_id is not None:
            result[current_id] = "\n".join(current_lines).rstrip("\n")
        current_id = None
        current_lines = []

    for line in cleaned:
        match = BLOCK_RE.match(line.strip())
        if match:
            flush()
            current_id = int(match.group(1))
            current_lines = []
            continue
        if current_id is not None:
            current_lines.append(line)

    flush()
    return result
