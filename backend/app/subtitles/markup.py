"""Markup protection for translation."""

from __future__ import annotations

import re
from dataclasses import dataclass

TAG_RE = re.compile(r"</?(?:i|b|u)>", re.IGNORECASE)


@dataclass
class MarkupProtection:
    protected_text: str
    tags: list[str]


def protect_markup(text: str) -> MarkupProtection:
    tags: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        idx = len(tags)
        tags.append(match.group(0))
        return f"<TAG{idx}>"

    protected = TAG_RE.sub(_replace, text)
    return MarkupProtection(protected_text=protected, tags=tags)


def restore_markup(text: str, tags: list[str]) -> str:
    result = text
    for idx, tag in enumerate(tags):
        result = result.replace(f"<TAG{idx}>", tag)
    return result


def extract_tag_sequence(text: str) -> list[str]:
    return TAG_RE.findall(text)
