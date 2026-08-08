"""Filename and language helpers."""

from __future__ import annotations

import re
from pathlib import Path

LANGUAGE_ALIASES: dict[str, str] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
    "pt": "pt",
    "pt-pt": "pt-PT",
    "pt-br": "pt-BR",
    "por": "pt",
    "portuguese": "pt",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "fr": "fr",
    "fre": "fr",
    "fra": "fr",
    "french": "fr",
    "de": "de",
    "ger": "de",
    "deu": "de",
    "german": "de",
    "it": "it",
    "ita": "it",
    "italian": "it",
}

# movie.en.srt, movie.en-US.srt, movie.English.srt, movie.srt
LANG_SUFFIX_RE = re.compile(
    r"^(?P<stem>.+?)\.(?P<lang>[A-Za-z]{2,3}(?:-[A-Za-z]{2})?|English|Portuguese|Spanish|French|German|Italian)\.srt$",
    re.IGNORECASE,
)
PLAIN_SRT_RE = re.compile(r"^(?P<stem>.+)\.srt$", re.IGNORECASE)


def normalize_language_code(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower().replace("_", "-")
    return LANGUAGE_ALIASES.get(key, value.strip())


def detect_language_from_filename(path: str | Path) -> str | None:
    name = Path(path).name
    match = LANG_SUFFIX_RE.match(name)
    if match:
        return normalize_language_code(match.group("lang"))
    return None


def build_target_subtitle_path(source_path: str | Path, target_language: str) -> Path:
    source = Path(source_path)
    name = source.name
    lang_match = LANG_SUFFIX_RE.match(name)
    if lang_match:
        stem = lang_match.group("stem")
    else:
        plain = PLAIN_SRT_RE.match(name)
        if not plain:
            raise ValueError(f"Not an SRT filename: {name}")
        stem = plain.group("stem")
    return source.with_name(f"{stem}.{target_language}.srt")


def build_external_subtitle_path(media_path: str | Path, language: str) -> Path:
    """Build sidecar SRT path next to a media file, e.g. Movie.mkv -> Movie.en.srt."""
    media = Path(media_path)
    lang = normalize_language_code(language) or language
    return media.with_name(f"{media.stem}.{lang}.srt")


def languages_compatible(a: str | None, b: str | None) -> bool:
    """Exact match, or bare code vs regional (pt <-> pt-PT)."""
    na = normalize_language_code(a)
    nb = normalize_language_code(b)
    if not na or not nb:
        return False
    if na.lower() == nb.lower():
        return True
    la, lb = na.lower(), nb.lower()
    if "-" not in la and lb.startswith(la + "-"):
        return True
    if "-" not in lb and la.startswith(lb + "-"):
        return True
    return False


def language_matches(candidate: str | None, preferred: list[str]) -> bool:
    if not candidate:
        return False
    return any(languages_compatible(candidate, pref) for pref in preferred)


def find_source_srt_beside_media(
    media_path: str | Path,
    source_languages: list[str],
) -> tuple[Path, str] | None:
    media = Path(media_path)
    directory = media.parent
    if not directory.is_dir():
        return None

    stem = media.stem
    candidates: list[tuple[int, Path, str]] = []
    for path in directory.glob("*.srt"):
        lang = detect_language_from_filename(path)
        if lang and language_matches(lang, source_languages):
            # Prefer exact stem match
            priority = 0 if path.name.startswith(stem + ".") or path.stem.startswith(stem) else 1
            # Also accept files that share stem before language suffix
            lang_match = LANG_SUFFIX_RE.match(path.name)
            if lang_match and lang_match.group("stem") == stem:
                priority = 0
            candidates.append((priority, path, lang))
        elif lang is None and path.name == f"{stem}.srt":
            # Bare .srt next to media — treat as first preferred language
            default_lang = normalize_language_code(source_languages[0]) or "en"
            candidates.append((2, path, default_lang))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].name))
    _, path, lang = candidates[0]
    return path, lang
