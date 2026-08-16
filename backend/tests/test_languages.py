"""Language normalization tests."""

from __future__ import annotations

import pytest

from app.languages import (
    LanguageNormalizationError,
    get_language,
    list_languages,
    normalize_language,
)


def test_catalog_includes_required_codes():
    codes = {lang.code for lang in list_languages()}
    for code in ("en", "pt", "pt-PT", "pt-BR", "es", "fr", "de", "it"):
        assert code in codes


def test_normalize_pt_pt_aliases():
    for value in ("pt-PT", "Portuguese (Portugal)", "Português de Portugal", "pt-pt"):
        lang = normalize_language(value)
        assert lang.code == "pt-PT"
        assert lang.display_name == "Portuguese (Portugal)"


def test_pt_br_remains_distinct():
    assert normalize_language("pt-BR").code == "pt-BR"
    assert normalize_language("Portuguese (Brazil)").code == "pt-BR"
    assert normalize_language("pt-PT").code != normalize_language("pt-BR").code


def test_bare_pt_is_generic():
    lang = normalize_language("pt")
    assert lang.code == "pt"
    assert lang.code != "pt-PT"


def test_english_aliases():
    assert normalize_language("English").code == "en"
    assert normalize_language("en-US").code == "en"


def test_invalid_language_raises():
    with pytest.raises(LanguageNormalizationError) as exc:
        normalize_language("xx-UNKNOWN")
    assert exc.value.code == "language_unrecognized"


def test_empty_language_raises():
    with pytest.raises(LanguageNormalizationError):
        normalize_language("  ")


def test_get_language():
    assert get_language("pt-PT") is not None
    assert get_language("nope") is None
