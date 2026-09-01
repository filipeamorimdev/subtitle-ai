"""OCR helpers for turning PGS bitmap subtitles into SRT."""

from __future__ import annotations

import functools
import re
import shutil
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from app.core.logging import get_logger
from app.subtitles.filenames import normalize_language_code
from app.subtitles.models import SubtitleBlock, SubtitleDocument
from app.subtitles.pgs import iter_pgs_events
from app.subtitles.writer.srt import write_srt_atomic

logger = get_logger("ocr")

# ISO 639-1 → Tesseract traineddata names shipped in the Docker image.
TESSERACT_LANGS: dict[str, str] = {
    "en": "eng",
    "pt": "por",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "nl": "nld",
    "pl": "pol",
    "ru": "rus",
    "ja": "jpn",
    "zh": "chi_sim",
    "ko": "kor",
    "ar": "ara",
    "sv": "swe",
    "da": "dan",
    "fi": "fin",
    "no": "nor",
    "tr": "tur",
    "hu": "hun",
    "cs": "ces",
    "el": "ell",
    "he": "heb",
    "hi": "hin",
    "th": "tha",
    "vi": "vie",
    "uk": "ukr",
    "ca": "cat",
    "ro": "ron",
}

_LETTER_RE = re.compile(r"[A-Za-zÀ-ÿ\u00c0-\u024f\u0400-\u04ff\u3040-\u30ff\u4e00-\u9fff]")


class OcrError(Exception):
    pass


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int], None]
OCR_IMAGE_TIMEOUT_SECONDS = 120.0


def ocr_available() -> bool:
    if shutil.which("tesseract") is None:
        return False
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


@functools.lru_cache(maxsize=1)
def list_tesseract_langs() -> frozenset[str]:
    if not ocr_available():
        return frozenset()
    try:
        import pytesseract

        langs = pytesseract.get_languages(config="")
        return frozenset(str(item) for item in langs if item and item != "osd")
    except Exception:  # noqa: BLE001
        return frozenset()


def tesseract_lang_for(language: str | None) -> str:
    raw = normalize_language_code(language) or (language or "en")
    base = raw.split("-", 1)[0].strip().lower()
    mapped = TESSERACT_LANGS.get(base, "eng")
    available = list_tesseract_langs()
    if not available:
        return mapped
    if mapped in available:
        return mapped
    if "eng" in available:
        logger.warning("Tesseract language %s is not installed; falling back to eng", mapped)
        return "eng"
    return next(iter(sorted(available)))


def ms_to_srt_timestamp(ms: int) -> str:
    if ms < 0:
        ms = 0
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def prepare_for_ocr(image: Image.Image) -> Image.Image:
    """Flatten PGS RGBA onto a high-contrast page Tesseract prefers."""
    rgba = image.convert("RGBA")
    raw = rgba.tobytes()
    lum_sum = 0
    count = 0
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
        if a < 32:
            continue
        lum_sum += int(0.299 * r + 0.587 * g + 0.114 * b)
        count += 1
    light_text = count == 0 or (lum_sum / count) >= 96
    background = (0, 0, 0, 255) if light_text else (255, 255, 255, 255)
    canvas = Image.new("RGBA", rgba.size, background)
    canvas.paste(rgba, mask=rgba.split()[-1])
    gray = ImageOps.grayscale(canvas.convert("RGB"))
    if light_text:
        gray = ImageOps.invert(gray)
    # Small PGS glyphs OCR better when upscaled.
    width, height = gray.size
    gray = gray.resize((width * 2, height * 2), Image.Resampling.LANCZOS)
    return ImageOps.autocontrast(gray)


def ocr_image(
    image: Image.Image,
    tess_lang: str,
    *,
    timeout: float = OCR_IMAGE_TIMEOUT_SECONDS,
) -> str:
    import pytesseract

    prepared = prepare_for_ocr(image)
    config = "--oem 1 --psm 6 -c preserve_interword_spaces=1"
    raw = pytesseract.image_to_string(prepared, lang=tess_lang, config=config, timeout=timeout)
    return _clean_ocr_text(raw)


def pgs_sup_to_srt(
    sup_bytes: bytes,
    output_path: str | Path,
    *,
    language: str | None = "en",
    overwrite: bool = False,
    is_cancelled: CancelCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """OCR a demuxed `.sup` stream and write a sidecar SRT."""
    if not ocr_available():
        raise OcrError("Tesseract OCR is not installed.")
    events = iter_pgs_events(sup_bytes)
    if not events:
        raise OcrError("PGS track contained no displayable subtitle images.")
    tess_lang = tesseract_lang_for(language)
    blocks: list[SubtitleBlock] = []
    empty = 0
    total = len(events)
    if progress_callback:
        progress_callback(0, total)
    for index, event in enumerate(events, start=1):
        if is_cancelled and is_cancelled():
            raise OcrError("PGS OCR cancelled.")
        try:
            text = ocr_image(event.image, tess_lang)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed for cue at %sms: %s", event.start_ms, exc)
            empty += 1
            if progress_callback:
                progress_callback(index, total)
            continue
        if is_cancelled and is_cancelled():
            raise OcrError("PGS OCR cancelled.")
        if not text:
            empty += 1
            if progress_callback:
                progress_callback(index, total)
            continue
        blocks.append(
            SubtitleBlock(
                index=len(blocks) + 1,
                start=ms_to_srt_timestamp(event.start_ms),
                end=ms_to_srt_timestamp(event.end_ms),
                text=text,
                original_text=text,
            )
        )
        if progress_callback:
            progress_callback(index, total)
    if not blocks:
        raise OcrError("OCR produced no readable text from the PGS track.")
    if empty:
        logger.info("PGS OCR dropped %s empty/failed cues of %s", empty, len(events))
    path = Path(output_path)
    document = SubtitleDocument(format="srt", encoding="utf-8", blocks=blocks)
    write_srt_atomic(path, document, overwrite=overwrite)
    return path


def _clean_ocr_text(raw: str) -> str:
    lines: list[str] = []
    for line in raw.replace("\r", "").split("\n"):
        cleaned = " ".join(line.replace("|", "I").split())
        if cleaned:
            lines.append(cleaned)
    text = "\n".join(lines).strip()
    if not text or _LETTER_RE.search(text) is None:
        return ""
    return text
