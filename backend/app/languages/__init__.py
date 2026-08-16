"""Canonical language catalog and normalization (backend-authoritative).

The curated dropdown is LANGUAGE_CATALOG (GET /api/languages). Typed input is
validated against ISO 639-1 / optional ISO 3166-1 region, plus catalog aliases.
Bare ``pt`` stays generic Portuguese and is never promoted to ``pt-PT``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.languages.iso import (
    ISO_639_1,
    LANGUAGE_NAMES,
    REGION_ALIASES,
    is_iso_3166_1,
    is_iso_639_1,
    language_name,
    region_name,
)


@dataclass(frozen=True)
class Language:
    code: str
    display_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Curated dropdown for v0.3. Frontend uses GET /api/languages.
LANGUAGE_CATALOG: tuple[Language, ...] = (
    Language(
        code="en",
        display_name="English",
        aliases=("eng", "english", "en-us", "en-gb", "en-US", "en-GB"),
    ),
    Language(
        code="pt",
        display_name="Portuguese",
        aliases=("por", "portuguese", "português", "portugues"),
    ),
    Language(
        code="pt-PT",
        display_name="Portuguese (Portugal)",
        aliases=(
            "pt-pt",
            "portuguese (portugal)",
            "portuguese portugal",
            "português de portugal",
            "portugues de portugal",
            "português (portugal)",
            "portugues (portugal)",
        ),
    ),
    Language(
        code="pt-BR",
        display_name="Portuguese (Brazil)",
        aliases=(
            "pt-br",
            "portuguese (brazil)",
            "portuguese brazil",
            "brazilian portuguese",
            "português do brasil",
            "portugues do brasil",
            "português (brasil)",
            "portugues (brasil)",
            "português brasileiro",
            "portugues brasileiro",
        ),
    ),
    Language(
        code="es",
        display_name="Spanish",
        aliases=("spa", "spanish", "español", "espanol", "castellano"),
    ),
    Language(
        code="fr",
        display_name="French",
        aliases=("fre", "fra", "french", "français", "francais"),
    ),
    Language(
        code="de",
        display_name="German",
        aliases=("ger", "deu", "german", "deutsch"),
    ),
    Language(
        code="it",
        display_name="Italian",
        aliases=("ita", "italian", "italiano"),
    ),
)


class LanguageNormalizationError(ValueError):
    """Raised when a language string cannot be normalized."""

    def __init__(self, message: str, *, code: str = "language_unrecognized") -> None:
        super().__init__(message)
        self.code = code


_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[_-][A-Za-z]{2,8})?$")
_NAME_REGION_RE = re.compile(
    r"^(?P<lang>.+?)\s*[\(/]\s*(?P<region>.+?)\s*\)?$"
)


def _alias_key(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", "-").split())


def _build_lookup() -> dict[str, Language]:
    lookup: dict[str, Language] = {}
    for lang in LANGUAGE_CATALOG:
        lookup[_alias_key(lang.code)] = lang
        lookup[_alias_key(lang.display_name)] = lang
        for alias in lang.aliases:
            lookup[_alias_key(alias)] = lang
    return lookup


_LOOKUP = _build_lookup()


def list_languages() -> list[Language]:
    return list(LANGUAGE_CATALOG)


def get_language(code: str) -> Language | None:
    if not code:
        return None
    found = _LOOKUP.get(_alias_key(code))
    if found is not None:
        return found
    try:
        return normalize_language(code)
    except LanguageNormalizationError:
        return None


def _display_for(lang_code: str, region: str | None) -> str:
    name = language_name(lang_code)
    if not region:
        return name
    return f"{name} ({region_name(region)})"


def _from_iso(lang_code: str, region: str | None = None) -> Language:
    lang_code = lang_code.lower()
    region_code = region.upper() if region else None
    code = f"{lang_code}-{region_code}" if region_code else lang_code
    # Prefer catalog display/aliases when the code is curated.
    catalog = _LOOKUP.get(_alias_key(code))
    if catalog is not None:
        return catalog
    return Language(code=code, display_name=_display_for(lang_code, region_code))


def _parse_bcp47(value: str) -> Language | None:
    token = value.strip().replace("_", "-")
    if not _TAG_RE.match(token):
        return None
    parts = token.split("-")
    lang = parts[0].lower()
    if len(lang) == 3:
        # ISO 639-2/B aliases that the catalog already covers (eng, por, …).
        return None
    if not is_iso_639_1(lang):
        return None
    region: str | None = None
    if len(parts) == 2:
        region_part = parts[1]
        if len(region_part) != 2 or not is_iso_3166_1(region_part):
            return None
        region = region_part.upper()
    elif len(parts) > 2:
        return None
    return _from_iso(lang, region)


def _lookup_language_name(name: str) -> str | None:
    key = _alias_key(name)
    catalog = _LOOKUP.get(key)
    if catalog is not None:
        # Name resolved to a catalog entry — only use the language subtag.
        return catalog.code.split("-", 1)[0].lower()
    if key in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[key]
    for code, english in ISO_639_1.items():
        if _alias_key(english) == key:
            return code
    return None


def _lookup_region_name(name: str) -> str | None:
    key = _alias_key(name)
    if len(key) == 2 and is_iso_3166_1(key):
        return key.upper()
    mapped = REGION_ALIASES.get(key)
    if mapped:
        return mapped
    return None


def _parse_named(value: str) -> Language | None:
    key = _alias_key(value)
    # "Japanese (Japan)" or "Korean/Korea"
    match = _NAME_REGION_RE.match(value.strip())
    if match:
        lang_code = _lookup_language_name(match.group("lang"))
        region = _lookup_region_name(match.group("region"))
        if lang_code and region:
            return _from_iso(lang_code, region)
        return None
    lang_code = _lookup_language_name(key)
    if lang_code:
        # Bare English name: Japanese → ja (not ja-JP).
        return _from_iso(lang_code, None)
    return None


def normalize_language(value: str | None) -> Language:
    """Normalize free-form language input.

    Bare ``pt`` stays generic Portuguese (not promoted to pt-PT).
    ``pt-PT`` and ``pt-BR`` remain distinct.
    Unknown garbage is rejected.
    """
    if value is None or not str(value).strip():
        raise LanguageNormalizationError("Language is required")
    raw = str(value).strip()
    key = _alias_key(raw)

    lang = _LOOKUP.get(key)
    if lang is not None:
        return lang

    tagged = _parse_bcp47(raw)
    if tagged is not None:
        return tagged

    named = _parse_named(raw)
    if named is not None:
        return named

    raise LanguageNormalizationError(
        f"Requested language could not be recognized: {value!r}"
    )
