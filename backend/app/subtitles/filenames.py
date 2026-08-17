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

# movie.en.srt, movie.en-US.srt, movie.en.hi.srt, movie.English.srt, movie.srt
LANG_SUFFIX_RE = re.compile(
    r"^(?P<stem>.+?)"
    r"\.(?P<lang>[A-Za-z]{2,3}(?:-[A-Za-z]{2})?|English|Portuguese|Spanish|French|German|Italian)"
    r"(?:\.(?P<flag>hi|sdh|forced|cc))?"
    r"\.srt$",
    re.IGNORECASE,
)
PLAIN_SRT_RE = re.compile(r"^(?P<stem>.+)\.srt$", re.IGNORECASE)
HI_FLAGS = frozenset({"hi", "sdh", "cc"})


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


def is_hi_subtitle_filename(path: str | Path) -> bool:
    match = LANG_SUFFIX_RE.match(Path(path).name)
    if not match:
        return False
    flag = (match.group("flag") or "").lower()
    return flag in HI_FLAGS


def _subtitle_media_stem(name: str) -> str:
    """Return the media stem with all trailing language/flag suffixes removed.

    Examples:
      movie.en.srt          -> movie
      movie.en.hi.srt       -> movie
      movie.en.pt-PT.srt    -> movie   (stacked tags; never keep source lang)
      movie.srt             -> movie
    """
    current = name
    while True:
        lang_match = LANG_SUFFIX_RE.match(current)
        if not lang_match:
            plain = PLAIN_SRT_RE.match(current)
            if not plain:
                raise ValueError(f"Not an SRT filename: {name}")
            return plain.group("stem")
        stem = lang_match.group("stem")
        # Peel another language tag if the stem itself looks like an SRT name.
        nxt = f"{stem}.srt"
        if not LANG_SUFFIX_RE.match(nxt):
            return stem
        current = nxt


def build_target_subtitle_path(
    source_path: str | Path,
    target_language: str,
    *,
    media_path: str | Path | None = None,
) -> Path:
    """Build target sidecar path: ``{mediaStem}.{target}.srt`` (source lang omitted).

    When ``media_path`` is a video (or any non-``.srt`` file), the media stem is
    used directly. Otherwise language tags are stripped from the source SRT name.
    """
    if media_path is not None:
        media = Path(media_path)
        if media.suffix and media.suffix.lower() != ".srt":
            return build_external_subtitle_path(media, target_language)

    source = Path(source_path)
    stem = _subtitle_media_stem(source.name)
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


def language_chip_matches_task(task_code: str | None, chip_code: str | None) -> bool:
    """Attach a localization task to a language chip.

    Exact match always. A generic task (``pt``) may overlay regional chips
    (``pt-PT``). A regional task must not appear on the generic chip — otherwise
    Portuguese (Portugal) looks like it is also generic Portuguese.
    """
    nt = normalize_language_code(task_code)
    nc = normalize_language_code(chip_code)
    if not nt or not nc:
        return False
    if nt.lower() == nc.lower():
        return True
    tl, cl = nt.lower(), nc.lower()
    return "-" not in tl and cl.startswith(tl + "-")


def language_matches(candidate: str | None, preferred: list[str]) -> bool:
    if not candidate:
        return False
    return any(languages_compatible(candidate, pref) for pref in preferred)


def subtitle_stem(path: str | Path) -> str | None:
    """Return the media stem embedded in an SRT filename, or None if not an SRT."""
    name = Path(path).name
    try:
        return _subtitle_media_stem(name)
    except ValueError:
        return None


def subtitle_belongs_to_media(subtitle_path: str | Path, media_path: str | Path) -> bool:
    """True when the SRT is a sidecar for this media file (same directory + stem)."""
    sub = Path(subtitle_path)
    media = Path(media_path)
    if sub.parent != media.parent:
        return False
    stem = subtitle_stem(sub)
    return stem is not None and stem == media.stem


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
        # Only sidecars for THIS media — never borrow another episode/movie's SRT
        # from the same folder (e.g. Season 1/*.en.srt).
        if subtitle_stem(path) != stem:
            continue
        lang = detect_language_from_filename(path)
        if lang and language_matches(lang, source_languages):
            hi_penalty = 1 if is_hi_subtitle_filename(path) else 0
            candidates.append((hi_penalty, path, lang))
        elif lang is None and path.name == f"{stem}.srt":
            # Bare .srt next to media — treat as first preferred language
            default_lang = normalize_language_code(source_languages[0]) or "en"
            candidates.append((2, path, default_lang))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].name))
    _, path, lang = candidates[0]
    return path, lang
