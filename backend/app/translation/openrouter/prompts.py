"""OpenRouter prompts — re-exports the provider-agnostic prompt module.

Kept so existing imports continue to work during the v0.3-alpha1 migration.
"""

from app.translation.prompts import (
    BLOCK_RE,
    MISSING_BLOCKS_PROMPT_EXTRA,
    SYSTEM_PROMPT,
    build_missing_repair_user_message,
    build_system_prompt,
    build_translate_user_message,
    format_batch,
    format_id_list,
    parse_batch_response,
)

__all__ = [
    "BLOCK_RE",
    "MISSING_BLOCKS_PROMPT_EXTRA",
    "SYSTEM_PROMPT",
    "build_missing_repair_user_message",
    "build_system_prompt",
    "build_translate_user_message",
    "format_batch",
    "format_id_list",
    "parse_batch_response",
]
