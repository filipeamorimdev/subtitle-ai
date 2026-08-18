"""Language normalization tests."""

from __future__ import annotations

import pytest

from app.languages import (
    LanguageNormalizationError,
    get_language,
    list_featured_languages,
    list_languages,
    normalize_language,
)
from app.languages.countries import COUNTRIES, flag_emoji
from app.languages.iso import ISO_3166_1


def test_catalog_includes_required_codes():
    codes = {lang.code for lang in list_languages()}
    for code in ("en", "pt", "pt-PT", "pt-BR", "es", "fr", "de", "it"):
        assert code in codes


def test_catalog_includes_country_locales_and_flags():
    catalog = list_languages()
    codes = {lang.code for lang in catalog}
    for code in ("en-US", "en-GB", "ja-JP", "ko-KR", "zh-CN", "zh-TW", "nl-NL", "pl-PL"):
        assert code in codes
    by_code = {lang.code: lang for lang in catalog}
    assert by_code["pt-PT"].flag == flag_emoji("PT")
    assert by_code["pt-BR"].flag == flag_emoji("BR")
    assert by_code["ja-JP"].region == "JP"
    assert by_code["en"].flag == flag_emoji("GB")
    assert all(lang.flag for lang in catalog)


def test_countries_cover_iso_3166_1():
    country_codes = {country.code for country in COUNTRIES}
    assert country_codes == set(ISO_3166_1)


def test_featured_catalog_is_compact():
    featured = {lang.code for lang in list_featured_languages()}
    catalog = list_languages()
    assert featured <= {lang.code for lang in catalog}
    assert 8 <= len(featured) <= 20
    assert len(catalog) > 200


def test_normalize_pt_pt_aliases():
    for value in ("pt-PT", "Portuguese (Portugal)", "Português de Portugal", "pt-pt", "Portugal"):
        lang = normalize_language(value)
        assert lang.code == "pt-PT"
        assert lang.display_name == "Portuguese (Portugal)"


def test_pt_br_remains_distinct():
    assert normalize_language("pt-BR").code == "pt-BR"
    assert normalize_language("Portuguese (Brazil)").code == "pt-BR"
    assert normalize_language("Português do Brasil").code == "pt-BR"
    assert normalize_language("Brazil").code == "pt-BR"
    assert normalize_language("pt-PT").code != normalize_language("pt-BR").code


def test_bare_pt_is_generic():
    lang = normalize_language("pt")
    assert lang.code == "pt"
    assert lang.code != "pt-PT"


def test_english_aliases():
    assert normalize_language("English").code == "en"
    assert normalize_language("en-US").code == "en-US"
    assert normalize_language("en-GB").code == "en-GB"
    assert normalize_language("United States").code == "en-US"
    assert normalize_language("United Kingdom").code == "en-GB"


def test_normalize_japanese_and_regional_codes():
    assert normalize_language("ja").code == "ja"
    assert normalize_language("Japanese").code == "ja"
    ja_jp = normalize_language("ja-JP")
    assert ja_jp.code == "ja-JP"
    assert "Japanese" in ja_jp.display_name
    named = normalize_language("Japanese (Japan)")
    assert named.code == "ja-JP"
    assert normalize_language("Japan").code == "ja-JP"


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


def test_bazarr_custom_language_codes():
    """Bazarr uses non-ISO alpha2 codes for LATAM Spanish, Brazilian Portuguese, Traditional Chinese."""
    latino = normalize_language("ea")
    assert latino.code == "ea"
    assert latino.display_name == "Spanish (Latin America)"
    assert get_language("spl").code == "ea"
    assert get_language("es-la").code == "ea"
    assert get_language("Spanish (Latino)").code == "ea"

    assert normalize_language("pb").code == "pt-BR"
    assert normalize_language("pob").code == "pt-BR"
    assert normalize_language("zt").code == "zh-TW"
