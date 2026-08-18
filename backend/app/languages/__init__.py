"""Canonical language catalog and normalization (backend-authoritative).

The dropdown is LANGUAGE_CATALOG (GET /api/languages): curated languages plus
one locale per country (with flag). Typed input is validated against ISO 639-1 /
optional ISO 3166-1 region, catalog aliases, and country names.
Bare ``pt`` stays generic Portuguese and is never promoted to ``pt-PT``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.languages.countries import COUNTRIES, country_languages, flag_emoji
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
    region: str | None = None
    flag: str = "🏳️"


# ISO 639-2/B (and common 639-2/T) aliases for dropdown search + typed input.
_ISO_639_2: dict[str, tuple[str, ...]] = {
    "af": ("afr",),
    "am": ("amh",),
    "ar": ("ara",),
    "az": ("aze",),
    "be": ("bel",),
    "bg": ("bul",),
    "bn": ("ben",),
    "bs": ("bos",),
    "ca": ("cat",),
    "cs": ("cze", "ces"),
    "cy": ("wel", "cym"),
    "da": ("dan",),
    "de": ("ger", "deu"),
    "el": ("gre", "ell"),
    "en": ("eng",),
    "es": ("spa",),
    "et": ("est",),
    "eu": ("baq", "eus"),
    "fa": ("per", "fas"),
    "fi": ("fin",),
    "fr": ("fre", "fra"),
    "ga": ("gle",),
    "gl": ("glg",),
    "he": ("heb",),
    "hi": ("hin",),
    "hr": ("hrv",),
    "hu": ("hun",),
    "hy": ("arm", "hye"),
    "id": ("ind",),
    "is": ("ice", "isl"),
    "it": ("ita",),
    "ja": ("jpn",),
    "ka": ("geo", "kat"),
    "kk": ("kaz",),
    "km": ("khm",),
    "ko": ("kor",),
    "lt": ("lit",),
    "lv": ("lav",),
    "mk": ("mac", "mkd"),
    "ms": ("may", "msa"),
    "nl": ("dut", "nld"),
    "no": ("nor",),
    "nb": ("nob",),
    "nn": ("nno",),
    "pl": ("pol",),
    "pt": ("por",),
    "ro": ("rum", "ron"),
    "ru": ("rus",),
    "sk": ("slo", "slk"),
    "sl": ("slv",),
    "sq": ("alb", "sqi"),
    "sr": ("srp",),
    "sv": ("swe",),
    "sw": ("swa",),
    "ta": ("tam",),
    "th": ("tha",),
    "tr": ("tur",),
    "uk": ("ukr",),
    "ur": ("urd",),
    "vi": ("vie",),
    "zh": ("chi", "zho"),
}

# Representative flag when the catalog entry is a bare language (no region in the code).
_DEFAULT_REGION: dict[str, str] = {
    "ar": "SA",
    "en": "GB",
    "es": "ES",
    "fa": "IR",
    "fr": "FR",
    "pt": "PT",
    "sw": "TZ",
    "zh": "CN",
}

# Shown first in the dropdown (common subtitle languages + key locales).
_FEATURED_CODES: tuple[str, ...] = (
    "en",
    "pt",
    "pt-PT",
    "pt-BR",
    "es",
    "fr",
    "de",
    "it",
    "ja",
    "ko",
    "zh-CN",
    "zh-TW",
    "ru",
    "ar",
    "nl",
    "pl",
)


def _lang(
    code: str,
    display_name: str,
    aliases: tuple[str, ...] = (),
    region: str | None = None,
) -> Language:
    flag_region = region or _default_region(code.split("-", 1)[0])
    return Language(
        code=code,
        display_name=display_name,
        aliases=aliases,
        region=flag_region,
        flag=flag_emoji(flag_region),
    )


def _default_region(lang_code: str) -> str | None:
    if lang_code in _DEFAULT_REGION:
        return _DEFAULT_REGION[lang_code]
    for country in COUNTRIES:
        if country.languages and country.languages[0] == lang_code:
            return country.code
    for country in COUNTRIES:
        if lang_code in country.languages:
            return country.code
    return None


# Curated entries keep rich aliases. Country locales are generated around them.
_CURATED: tuple[Language, ...] = (
    _lang(
        "en",
        "English",
        ("eng", "english"),
        "GB",
    ),
    _lang(
        "pt",
        "Portuguese",
        ("por", "portuguese", "português", "portugues"),
        "PT",
    ),
    _lang(
        "pt-PT",
        "Portuguese (Portugal)",
        (
            "pt-pt",
            "portuguese (portugal)",
            "portuguese portugal",
            "português de portugal",
            "portugues de portugal",
            "português (portugal)",
            "portugues (portugal)",
        ),
        "PT",
    ),
    _lang(
        "pt-BR",
        "Portuguese (Brazil)",
        (
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
            "pb",
            "pob",
        ),
        "BR",
    ),
    _lang(
        "es",
        "Spanish",
        ("spa", "spanish", "español", "espanol", "castellano"),
        "ES",
    ),
    # Bazarr custom alpha2: ``es`` is Spain Spanish, so LATAM uses ``ea``.
    _lang(
        "ea",
        "Spanish (Latin America)",
        (
            "spl",
            "es-la",
            "spa-la",
            "es-419",
            "spanish (latino)",
            "spanish (latin america)",
            "spanish (latinoamérica)",
            "spanish (latinoamerica)",
            "latin american spanish",
            "latam spanish",
        ),
        "MX",
    ),
    _lang(
        "fr",
        "French",
        ("fre", "fra", "french", "français", "francais"),
        "FR",
    ),
    _lang(
        "de",
        "German",
        ("ger", "deu", "german", "deutsch"),
        "DE",
    ),
    _lang(
        "it",
        "Italian",
        ("ita", "italian", "italiano"),
        "IT",
    ),
    _lang("ja", "Japanese", ("jpn", "japanese", "日本語"), "JP"),
    _lang("ko", "Korean", ("kor", "korean", "한국어", "한국말"), "KR"),
    _lang("zh", "Chinese", ("chi", "zho", "chinese", "mandarin", "中文"), "CN"),
    _lang(
        "zh-CN",
        "Chinese (China)",
        (
            "zh-cn",
            "zh-hans",
            "simplified chinese",
            "chinese simplified",
            "chinese (simplified)",
            "简体",
            "简体中文",
        ),
        "CN",
    ),
    _lang(
        "zh-TW",
        "Chinese (Taiwan)",
        (
            "zh-tw",
            "zh-hant",
            "traditional chinese",
            "chinese traditional",
            "chinese (traditional)",
            "繁體",
            "繁體中文",
            "zt",
            "zht",
        ),
        "TW",
    ),
    _lang("ru", "Russian", ("rus", "russian", "русский"), "RU"),
    _lang("ar", "Arabic", ("ara", "arabic", "العربية"), "SA"),
    _lang("nl", "Dutch", ("dut", "nld", "dutch", "nederlands"), "NL"),
    _lang("pl", "Polish", ("pol", "polish", "polski"), "PL"),
)


def _country_locale_aliases(lang_code: str, country, code: str) -> tuple[str, ...]:
    display = f"{language_name(lang_code)} ({country.name})"
    aliases = [code.lower(), display.lower()]
    if country.languages and country.languages[0] == lang_code:
        aliases.append(country.name)
        aliases.extend(country.aliases)
    return tuple(dict.fromkeys(aliases))


def _generic_aliases(lang_code: str, display_name: str) -> tuple[str, ...]:
    aliases = [display_name.lower(), *_ISO_639_2.get(lang_code, ())]
    for name, mapped in LANGUAGE_NAMES.items():
        if mapped == lang_code:
            aliases.append(name)
    return tuple(dict.fromkeys(aliases))


def _build_catalog() -> tuple[Language, ...]:
    by_code: dict[str, Language] = {}
    for lang in _CURATED:
        by_code[lang.code] = lang

    for country in COUNTRIES:
        for lang_code in country.languages:
            if not is_iso_639_1(lang_code):
                continue
            code = f"{lang_code}-{country.code}"
            extra = _country_locale_aliases(lang_code, country, code)
            existing = by_code.get(code)
            if existing is not None:
                merged = tuple(dict.fromkeys((*existing.aliases, *extra)))
                by_code[code] = _lang(
                    existing.code,
                    existing.display_name,
                    merged,
                    existing.region,
                )
                continue
            by_code[code] = _lang(
                code,
                f"{language_name(lang_code)} ({country.name})",
                extra,
                country.code,
            )

    for lang_code, name in ISO_639_1.items():
        if lang_code in by_code:
            continue
        by_code[lang_code] = _lang(
            lang_code,
            name,
            _generic_aliases(lang_code, name),
            _default_region(lang_code),
        )

    featured = [by_code[code] for code in _FEATURED_CODES if code in by_code]
    featured_codes = {lang.code for lang in featured}
    rest = sorted(
        (lang for lang in by_code.values() if lang.code not in featured_codes),
        key=lambda lang: (lang.display_name.lower(), lang.code),
    )
    return tuple(featured + rest)


LANGUAGE_CATALOG: tuple[Language, ...] = _build_catalog()


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
            lookup.setdefault(_alias_key(alias), lang)
    return lookup


def _build_region_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {key: value.upper() for key, value in REGION_ALIASES.items()}
    for country in COUNTRIES:
        lookup.setdefault(_alias_key(country.name), country.code)
        lookup.setdefault(_alias_key(country.code), country.code)
        for alias in country.aliases:
            lookup.setdefault(_alias_key(alias), country.code)
    return lookup


_LOOKUP = _build_lookup()
_REGION_LOOKUP = _build_region_lookup()


def list_languages() -> list[Language]:
    return list(LANGUAGE_CATALOG)


def list_featured_languages() -> list[Language]:
    """Compact set for media chips; the dropdown uses the full catalog."""
    by_code = {lang.code: lang for lang in LANGUAGE_CATALOG}
    return [by_code[code] for code in _FEATURED_CODES if code in by_code]


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
    catalog = _LOOKUP.get(_alias_key(code))
    if catalog is not None:
        return catalog
    return _lang(code, _display_for(lang_code, region_code), (), region_code)


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
    mapped = _REGION_LOOKUP.get(key)
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


def _parse_country(value: str) -> Language | None:
    region = _lookup_region_name(value)
    if not region:
        return None
    langs = country_languages(region)
    if not langs:
        return None
    return _from_iso(langs[0], region)


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

    country = _parse_country(raw)
    if country is not None:
        return country

    raise LanguageNormalizationError(
        f"Requested language could not be recognized: {value!r}"
    )
