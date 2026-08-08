"""Translation prompts and batch formatting."""

from __future__ import annotations

import re

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

CORRECTION_PROMPT_EXTRA = """
Your previous response failed validation. Return ONLY the block mapping in this exact format:

[001]
translated line

[002]
translated line

Every input ID must appear exactly once. Do not add commentary.
"""

BLOCK_RE = re.compile(r"^\[(\d{3,})\]\s*$")


def build_system_prompt(target_language_code: str, target_language_name: str) -> str:
    locale_note = ""
    if target_language_code.lower() in {"pt-pt", "pt"} and "brazil" not in target_language_name.lower():
        locale_note = (
            "\nUse European Portuguese (PT-PT), not Brazilian Portuguese (PT-BR).\n"
        )
    return (
        SYSTEM_PROMPT
        + f"\nTarget language code: {target_language_code}\n"
        + f"Target language name: {target_language_name}\n"
        + locale_note
    )


def format_batch(blocks: list[tuple[int, str]]) -> str:
    parts: list[str] = []
    for block_id, text in blocks:
        parts.append(f"[{block_id:03d}]")
        parts.append(text)
        parts.append("")
    return "\n".join(parts).strip() + "\n"


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
