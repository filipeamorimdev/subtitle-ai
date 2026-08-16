"""Canonical language catalog and normalization (backend-authoritative)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    code: str
    display_name: str
    aliases: tuple[str, ...] = field(default_factory=tuple)


# Minimum catalog for v0.3. Frontend dropdown uses GET /api/languages.
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
    return _LOOKUP.get(_alias_key(code))


def normalize_language(value: str | None) -> Language:
    """Normalize free-form language input to a catalog Language.

    Bare ``pt`` stays generic Portuguese (not promoted to pt-PT).
    ``pt-PT`` and ``pt-BR`` remain distinct.
    """
    if value is None or not str(value).strip():
        raise LanguageNormalizationError("Language is required")
    key = _alias_key(str(value))
    # Exact code match preserving regional casing from catalog.
    lang = _LOOKUP.get(key)
    if lang is not None:
        return lang
    # Accept already-canonical codes that only differ by case (e.g. PT-pt).
    for candidate in LANGUAGE_CATALOG:
        if candidate.code.lower() == key.lower():
            return candidate
    raise LanguageNormalizationError(
        f"Requested language could not be recognized: {value!r}"
    )
