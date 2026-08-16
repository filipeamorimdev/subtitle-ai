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
    assert normalize_language("Português do Brasil").code == "pt-BR"
    assert normalize_language("pt-PT").code != normalize_language("pt-BR").code


def test_bare_pt_is_generic():
    lang = normalize_language("pt")
    assert lang.code == "pt"
    assert lang.code != "pt-PT"


def test_english_aliases():
    assert normalize_language("English").code == "en"
    assert normalize_language("en-US").code == "en"


def test_normalize_japanese_and_regional_codes():
    assert normalize_language("ja").code == "ja"
    assert normalize_language("Japanese").code == "ja"
    ja_jp = normalize_language("ja-JP")
    assert ja_jp.code == "ja-JP"
    assert "Japanese" in ja_jp.display_name
    named = normalize_language("Japanese (Japan)")
    assert named.code == "ja-JP"


def test_korean_dutch_polish():
    assert normalize_language("ko").code == "ko"
    assert normalize_language("ko-KR").code == "ko-KR"
    korea = normalize_language("Korean/Korea")
    assert korea.code == "ko-KR"
    assert normalize_language("nl").code == "nl"
    assert normalize_language("nl-NL").code == "nl-NL"
    assert normalize_language("pl").code == "pl"
    assert normalize_language("pl-PL").code == "pl-PL"


def test_invalid_language_raises():
    with pytest.raises(LanguageNormalizationError) as exc:
        normalize_language("xx-UNKNOWN")
    assert exc.value.code == "language_unrecognized"
    with pytest.raises(LanguageNormalizationError):
        normalize_language("not-a-real-language-zzz")


def test_empty_language_raises():
    with pytest.raises(LanguageNormalizationError):
        normalize_language("  ")


def test_get_language():
    assert get_language("pt-PT") is not None
    assert get_language("nope") is None
