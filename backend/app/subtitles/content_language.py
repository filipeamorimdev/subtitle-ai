"""Detect the spoken language of SRT dialogue.

Filename and Bazarr tags are often wrong: ``.hi.srt`` is ISO Hindi, but the
same suffix is also used for English hearing-impaired dumps. When the cues
themselves are clearly another language, trust the text.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.logging import get_logger
from app.subtitles.filenames import (
    detect_language_from_filename,
    languages_compatible,
    normalize_language_code,
)
from app.subtitles.parsers.srt import SrtParseError, parse_srt

logger = get_logger("subtitles.content_language")

_TOKEN_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)
_MARKUP_RE = re.compile(r"<[^>]+>|\{[^}]+\}|[♪♫\[\]()]")
_MIN_TOKENS = 16
_MIN_HITS = 5
_MIN_MARGIN = 2.0
_SCRIPT_RATIO = 0.35

# Distinctive function words. Shared tokens like "de" / "que" are omitted
# so a short French cue cannot outvote English (or vice versa).
_LATIN_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset(
        {
            "the",
            "and",
            "you",
            "we",
            "they",
            "this",
            "that",
            "with",
            "from",
            "have",
            "has",
            "had",
            "was",
            "were",
            "are",
            "not",
            "but",
            "what",
            "when",
            "will",
            "can",
            "don't",
            "i'm",
            "we're",
            "they're",
            "it's",
            "she's",
            "he's",
            "let's",
            "can't",
            "won't",
            "that's",
            "there's",
            "your",
            "our",
            "his",
            "her",
            "she",
            "him",
            "them",
            "then",
            "than",
            "just",
            "about",
            "into",
            "because",
            "if",
            "or",
            "for",
            "on",
            "at",
            "be",
            "to",
            "of",
            "is",
            "it",
            "my",
            "me",
            "i",
            "we",
            "you",
        }
    ),
    "pt": frozenset(
        {
            "não",
            "nao",
            "você",
            "voce",
            "está",
            "esta",
            "estão",
            "estao",
            "são",
            "sao",
            "para",
            "uma",
            "mais",
            "ela",
            "ele",
            "isso",
            "este",
            "esta",
            "pelo",
            "pela",
            "como",
            "mas",
            "muito",
            "também",
            "tambem",
            "agora",
            "aqui",
            "ainda",
            "vamos",
            "está",
        }
    ),
    "es": frozenset(
        {
            "está",
            "están",
            "esto",
            "esta",
            "pero",
            "como",
            "más",
            "una",
            "los",
            "las",
            "para",
            "con",
            "por",
            "qué",
            "que",
            "ella",
            "usted",
            "nosotros",
            "también",
            "ahora",
            "aquí",
        }
    ),
    "fr": frozenset(
        {
            "les",
            "des",
            "une",
            "vous",
            "nous",
            "est",
            "sont",
            "pas",
            "pour",
            "dans",
            "avec",
            "qui",
            "mais",
            "comme",
            "elle",
            "cette",
            "tout",
            "plus",
            "aussi",
            "je",
            "tu",
            "on",
        }
    ),
    "de": frozenset(
        {
            "und",
            "der",
            "die",
            "das",
            "nicht",
            "ich",
            "sie",
            "ein",
            "eine",
            "ist",
            "sind",
            "mit",
            "den",
            "dem",
            "auf",
            "von",
            "wir",
            "ihr",
            "auch",
            "aber",
            "noch",
        }
    ),
    "it": frozenset(
        {
            "che",
            "non",
            "una",
            "per",
            "con",
            "sono",
            "più",
            "piu",
            "ma",
            "come",
            "questo",
            "questa",
            "gli",
            "della",
            "anche",
            "più",
            "siamo",
            "hanno",
        }
    ),
}

_SCRIPT_LANGUAGE = {
    "devanagari": "hi",
    "arabic": "ar",
    "hangul": "ko",
    "cyrillic": "ru",
    "kana": "ja",
    "han": "zh",
}


def _script_bucket(char: str) -> str | None:
    code = ord(char)
    if 0x0900 <= code <= 0x097F:
        return "devanagari"
    if 0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F:
        return "arabic"
    if 0xAC00 <= code <= 0xD7AF:
        return "hangul"
    if 0x3040 <= code <= 0x30FF:
        return "kana"
    if 0x4E00 <= code <= 0x9FFF:
        return "han"
    if 0x0400 <= code <= 0x04FF:
        return "cyrillic"
    if (
        0x0041 <= code <= 0x005A
        or 0x0061 <= code <= 0x007A
        or 0x00C0 <= code <= 0x024F
    ):
        return "latin"
    return None


def _dominant_script_language(text: str) -> str | None:
    counts: dict[str, int] = {}
    letters = 0
    for char in text:
        if not char.isalpha():
            continue
        letters += 1
        bucket = _script_bucket(char)
        if bucket:
            counts[bucket] = counts.get(bucket, 0) + 1
    if letters < 12:
        return None
    best_script, best_count = max(counts.items(), key=lambda item: item[1], default=(None, 0))
    if best_script is None or best_count / letters < _SCRIPT_RATIO:
        return None
    if best_script == "latin":
        return None
    if best_script == "han" and counts.get("kana", 0) > 0:
        return "ja"
    return _SCRIPT_LANGUAGE.get(best_script)


def _clean_dialogue(text: str) -> str:
    return _MARKUP_RE.sub(" ", text)


def detect_language_from_text(text: str) -> str | None:
    """Return an ISO language when the sample is confident, else None."""
    cleaned = _clean_dialogue(text)
    script_lang = _dominant_script_language(cleaned)
    if script_lang:
        return script_lang
    tokens = [token.lower() for token in _TOKEN_RE.findall(cleaned)]
    if len(tokens) < _MIN_TOKENS:
        return None
    scores = {
        lang: sum(1 for token in tokens if token in words)
        for lang, words in _LATIN_STOPWORDS.items()
    }
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_lang, best_hits = ranked[0]
    second_hits = ranked[1][1] if len(ranked) > 1 else 0
    if best_hits < _MIN_HITS:
        return None
    if second_hits > 0 and best_hits < second_hits * _MIN_MARGIN:
        return None
    return best_lang


def detect_language_from_srt_path(path: str | Path) -> str | None:
    """Read an SRT and detect dialogue language, or None if unreadable/ambiguous."""
    file = Path(path)
    try:
        if not file.is_file() or file.stat().st_size <= 0:
            return None
        content = file.read_text(encoding="utf-8-sig", errors="replace")
        document = parse_srt(content)
    except (OSError, SrtParseError, ValueError):
        return None
    sample = "\n".join(block.text for block in document.blocks[:80])
    return detect_language_from_text(sample)


def refine_subtitle_language(
    path: str | Path,
    *,
    claimed: str | None = None,
) -> str | None:
    """Prefer dialogue language when it disagrees with the filename/Bazarr tag."""
    tagged = claimed if claimed is not None else detect_language_from_filename(path)
    tagged = normalize_language_code(tagged) if tagged else None
    content = detect_language_from_srt_path(path)
    if content is None:
        return tagged
    if tagged and languages_compatible(content, tagged):
        return tagged
    if tagged and content != tagged:
        logger.info(
            "sidecar_language_corrected path=%s claimed=%s detected=%s",
            path,
            tagged,
            content,
        )
    return content
