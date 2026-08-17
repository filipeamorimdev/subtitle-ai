"""OCR helper tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.subtitles.ocr import (
    OcrError,
    _clean_ocr_text,
    ms_to_srt_timestamp,
    pgs_sup_to_srt,
    prepare_for_ocr,
    tesseract_lang_for,
)
from tests.test_pgs import _minimal_sup


def test_ms_to_srt_timestamp():
    assert ms_to_srt_timestamp(0) == "00:00:00,000"
    assert ms_to_srt_timestamp(3_661_234) == "01:01:01,234"


def test_clean_ocr_text_drops_garbage():
    assert _clean_ocr_text("  Hello | world  \n\n") == "Hello I world"
    assert _clean_ocr_text("...") == ""
    assert _clean_ocr_text("") == ""


def test_tesseract_lang_for(monkeypatch):
    monkeypatch.setattr("app.subtitles.ocr.list_tesseract_langs", lambda: frozenset({"eng", "fra"}))
    assert tesseract_lang_for("en") == "eng"
    assert tesseract_lang_for("fr") == "fra"
    assert tesseract_lang_for("pt-PT") == "eng"  # por missing → fallback


def test_prepare_for_ocr_inverts_light_text():
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for x in range(8):
        image.putpixel((x, 3), (255, 255, 255, 255))
    prepared = prepare_for_ocr(image)
    assert prepared.mode == "L"
    assert prepared.size == (16, 16)


def test_pgs_sup_to_srt_writes_cues(tmp_path, monkeypatch):
    monkeypatch.setattr("app.subtitles.ocr.ocr_available", lambda: True)
    monkeypatch.setattr("app.subtitles.ocr.tesseract_lang_for", lambda _: "eng")
    monkeypatch.setattr("app.subtitles.ocr.ocr_image", lambda *_args, **_kwargs: "Hello there")
    output = tmp_path / "show.en.srt"
    result = pgs_sup_to_srt(_minimal_sup(), output, language="en")
    text = Path(result).read_text(encoding="utf-8")
    assert "Hello there" in text
    assert "00:00:01,000 --> 00:00:03,000" in text


def test_pgs_sup_to_srt_requires_readable_text(tmp_path, monkeypatch):
    monkeypatch.setattr("app.subtitles.ocr.ocr_available", lambda: True)
    monkeypatch.setattr("app.subtitles.ocr.ocr_image", lambda *_args, **_kwargs: "")
    with pytest.raises(OcrError, match="no readable text"):
        pgs_sup_to_srt(_minimal_sup(), tmp_path / "empty.srt")
