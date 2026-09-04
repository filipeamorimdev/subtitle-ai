"""Filename and language helpers."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.logging import get_logger
from app.integrations.bazarr.paths import is_under_roots

logger = get_logger("subtitles.filenames")

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
    "ea": "ea",
    "spl": "ea",
    "es-la": "ea",
    "spa-la": "ea",
    "pb": "pt-BR",
    "pob": "pt-BR",
    "zt": "zh-TW",
    "zht": "zh-TW",
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

# On-disk sidecar tag for player/Bazarr compatibility. IETF ``pt-PT`` is the
# task language, but this library and Jellyfin/Bazarr already use ``.pt.srt``.
# Writing both names made Jellyfin list Portuguese twice.
SIDECAR_LANGUAGE_TAGS: dict[str, str] = {
    "pt-PT": "pt",
}


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
    tag = sidecar_language_tag(target_language)
    return source.with_name(f"{stem}.{tag}.srt")


def sidecar_language_tag(language: str) -> str:
    """Filename language tag: ``pt-PT`` → ``pt`` so Bazarr/Jellyfin see one file."""
    lang = normalize_language_code(language) or (language or "").strip()
    return SIDECAR_LANGUAGE_TAGS.get(lang, lang or "und")


def build_external_subtitle_path(media_path: str | Path, language: str) -> Path:
    """Build sidecar SRT path next to a media file, e.g. Movie.mkv -> Movie.en.srt."""
    media = Path(media_path)
    lang = sidecar_language_tag(language)
    return media.with_name(f"{media.stem}.{lang}.srt")


def build_dub_preview_path(media_path: str | Path, language: str) -> Path:
    """Build preview dub filename next to a media file.

    Example: ``Movie (2026).mkv`` + ``pt-PT`` -> ``Movie (2026).pt.dub.mkv``.
    """
    media = Path(media_path)
    lang = sidecar_language_tag(language)
    return media.with_name(f"{media.stem}.{lang}.dub.mkv")


def _sidecar_dir_and_stem(path: Path) -> tuple[Path, str]:
    path = Path(path)
    if path.suffix.lower() == ".srt":
        try:
            return path.parent, _subtitle_media_stem(path.name)
        except ValueError:
            return path.parent, path.stem
    return path.parent, path.stem


def find_existing_sidecar(path: str | Path, language: str) -> Path | None:
    """Non-empty sidecar for this language (canonical ``.pt.srt`` or leftover ``.pt-PT.srt``)."""
    parent, stem = _sidecar_dir_and_stem(Path(path))
    lang = normalize_language_code(language) or language
    tag = sidecar_language_tag(language)
    names = [f"{stem}.{tag}.srt"]
    if lang.lower() != tag.lower():
        names.append(f"{stem}.{lang}.srt")
    seen: set[Path] = set()
    for name in names:
        candidate = parent / name
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def ensure_canonical_sidecar(path: str | Path, language: str) -> Path:
    """Keep a single sidecar. Rename leftover IETF names, or delete them if canonical exists."""
    parent, stem = _sidecar_dir_and_stem(Path(path))
    tag = sidecar_language_tag(language)
    lang = normalize_language_code(language) or language
    canonical = parent / f"{stem}.{tag}.srt"
    if lang.lower() == tag.lower():
        return canonical
    leftover = parent / f"{stem}.{lang}.srt"
    if leftover.is_file() and leftover.stat().st_size > 0:
        try:
            same = leftover.resolve() == canonical.resolve()
        except OSError:
            same = leftover == canonical
        if not same:
            if not canonical.is_file() or canonical.stat().st_size <= 0:
                leftover.replace(canonical)
            else:
                leftover.unlink()
    return canonical


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


def language_chip_available(chip_code: str | None, present_codes: set[str] | list[str]) -> bool:
    """Whether this language chip has an actual subtitle file.

    Sibling locales are not interchangeable: a ``pt`` sidecar (written for
    Portuguese Portugal) must not light up Brazilian Portuguese.
    A regional chip may match its canonical sidecar (``pt-PT`` ↔ ``pt``).
    """
    nc = normalize_language_code(chip_code)
    if not nc:
        return False
    present_n = {
        (normalize_language_code(code) or code).lower()
        for code in present_codes
        if code
    }
    present_n.discard("")
    cl = nc.lower()
    if cl in present_n:
        return True
    tag = sidecar_language_tag(nc).lower()
    return "-" in cl and tag in present_n


def suppress_generic_language_chip(
    chip_code: str | None,
    present_codes: set[str] | list[str],
    regional_codes: list[str],
) -> bool:
    """Hide generic ``pt`` when ``pt-PT`` already represents the same sidecar."""
    nc = normalize_language_code(chip_code)
    if not nc or "-" in nc:
        return False
    if not language_chip_available(nc, present_codes):
        return False
    tag = sidecar_language_tag(nc).lower()
    prefix = nc.lower() + "-"
    for other in regional_codes:
        on = normalize_language_code(other)
        if not on or on == nc or not on.lower().startswith(prefix):
            continue
        if (
            language_chip_available(on, present_codes)
            and sidecar_language_tag(on).lower() == tag
        ):
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


def is_origin_language(
    language: str | None,
    *,
    preferred_languages: list[str] | None = None,
    target_language: str | None = None,
    allow_other_languages: bool = True,
    allow_unlabeled: bool = True,
) -> bool:
    """True when a sidecar/track can be used as a translation origin.

    Preferred languages rank local matches and are a hard filter only when
    ``allow_other_languages`` is false (Bazarr search for a specific code).
    The localization target is never treated as an origin.
    """
    if language and target_language and languages_compatible(language, target_language):
        return False
    if not language:
        return allow_unlabeled
    if preferred_languages and language_matches(language, preferred_languages):
        return True
    return allow_other_languages


def origin_language_rank(language: str | None, preferred_languages: list[str] | None) -> int:
    """Lower is better: preferred, then any other labeled language, then unlabeled."""
    preferred = preferred_languages or []
    if language and language_matches(language, preferred):
        return 0
    if language:
        return 1
    return 2


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
    *,
    target_language: str | None = None,
    allow_other_languages: bool = True,
) -> tuple[Path, str] | None:
    media = Path(media_path)
    directory = media.parent
    if not directory.is_dir():
        return None

    from app.subtitles.content_language import refine_subtitle_language

    stem = media.stem
    candidates: list[tuple[int, int, Path, str]] = []
    for path in directory.glob("*.srt"):
        # Only sidecars for THIS media — never borrow another episode/movie's SRT
        # from the same folder (e.g. Season 1/*.en.srt).
        if subtitle_stem(path) != stem:
            continue
        lang = refine_subtitle_language(path)
        if lang is None and path.name == f"{stem}.srt":
            if not is_origin_language(
                None,
                preferred_languages=source_languages,
                target_language=target_language,
                allow_other_languages=allow_other_languages,
            ):
                continue
            default_lang = (
                normalize_language_code(source_languages[0]) if source_languages else None
            ) or "und"
            candidates.append(
                (origin_language_rank(None, source_languages), 2, path, default_lang)
            )
            continue
        if not is_origin_language(
            lang,
            preferred_languages=source_languages,
            target_language=target_language,
            allow_other_languages=allow_other_languages,
            allow_unlabeled=False,
        ):
            continue
        hi_penalty = 1 if is_hi_subtitle_filename(path) else 0
        candidates.append(
            (origin_language_rank(lang, source_languages), hi_penalty, path, lang or "und")
        )

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].name))
    _, _, path, lang = candidates[0]
    return path, lang


def _same_sidecar_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def unlink_extracted_source(
    source_path: str | Path,
    *,
    target_path: str | Path | None = None,
    media_path: str | Path | None = None,
    media_roots: list[str] | None = None,
) -> bool:
    """Delete an extracted source sidecar. Returns True if the file was removed.

    Never deletes the translated target, the media file, or paths outside media roots.
    """
    source = Path(source_path)
    if source.suffix.lower() != ".srt":
        return False
    if media_path is not None and _same_sidecar_path(source, Path(media_path)):
        return False
    if target_path is not None and _same_sidecar_path(source, Path(target_path)):
        return False
    if media_roots is not None and not is_under_roots(source, media_roots):
        logger.warning("Refusing to delete extracted source outside media roots: %s", source)
        return False
    if not source.is_file():
        return False
    try:
        source.unlink()
    except OSError as exc:
        logger.warning("Could not delete extracted source %s: %s", source, exc)
        return False
    logger.info("Deleted extracted source sidecar %s", source)
    return True
