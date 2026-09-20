"""Hardcoded, self-contained OCR engine shared by every document extractor.

The default (hardcoded) engine is RapidOCR: it runs on the ONNX runtime, ships
recognition models with the wheel, and needs no system binary. pytesseract is
only consulted as an optional fallback when the Tesseract executable is actually
installed and on ``PATH``, so the pipeline works identically on any machine.

The public API is intentionally generic: it accepts plain PIL images or raw PDF
bytes and returns plain text, letting image uploads and scanned PDFs flow
through the same path everywhere (document analysis and bank statements).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Hardcoded engine tuning. Not environment-driven: the OCR stack is meant to
# behave identically everywhere without configuration.
_RENDER_ZOOM = 2.0  # pymupdf rasterization scale for scanned PDF pages
_LINE_BUCKET = 12  # vertical tolerance in px used to reconstruct text lines
_MIN_LONG_SIDE = 800  # upscale images smaller than this for better recognition
_MAX_LONG_SIDE = 2400  # cap upscaling so memory stays bounded
_UPSCALE_FACTOR = 2.0

_RAPID_OCR: Any | None = None
_TESSERACT_PROBED = False
_TESSERACT_AVAILABLE = False


class OCRUnavailableError(RuntimeError):
    """Raised when the underlying OCR backend cannot be started."""


def ocr_image(image: Any) -> str:
    """Run OCR on any PIL image and return line-ordered text (``""`` when empty).

    ``image`` may be an ``Image.Image`` or anything ``numpy`` can convert.
    """
    prepared = _prepare_image(image)
    text = _ocr_with_rapidocr(prepared)
    if text.strip():
        return text.strip()
    return _ocr_with_tesseract(prepared).strip()


def ocr_pdf(pdf_bytes: bytes, password: str = "") -> list[str]:
    """OCR every page of a scanned PDF and return one text string per page."""
    from PIL import Image

    parts: list[str] = []
    for image in rasterize_pdf(pdf_bytes, password):
        text = ocr_image(image) if isinstance(image, Image.Image) else ""
        if text:
            parts.append(text)
    if not parts:
        raise ValueError("No readable text was found in the scanned PDF.")
    return parts


def rasterize_pdf(pdf_bytes: bytes, password: str = "") -> list[Any]:
    """Render every PDF page to a PIL image; public so callers can inspect pages."""
    try:
        import pymupdf
    except ImportError as error:
        raise OCRUnavailableError(
            "PDF rendering is unavailable; install pymupdf to process scanned pages."
        ) from error

    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    _unlock_pdf(document, password)
    images: list[Any] = []
    for page in document:
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(_RENDER_ZOOM, _RENDER_ZOOM), alpha=False
        )
        from PIL import Image

        images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
    if not images:
        raise ValueError("The PDF has no pages to read.")
    return images


def text_from_boxes(result: object) -> str:
    """Reconstruct readable lines from RapidOCR box results.

    Tokens are grouped by vertical position (``_LINE_BUCKET`` tolerance) and
    sorted left-to-right inside each line, which preserves the reading order
    that matters for tables such as bank statements.
    """
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    if boxes is None or texts is None:
        return ""
    items: list[tuple[float, float, str]] = []
    for box, text in zip(boxes, texts, strict=False):
        token = str(text).strip()
        if not token:
            continue
        try:
            points = list(box)
            ys = [float(point[1]) for point in points]
            xs = [float(point[0]) for point in points]
        except Exception:
            continue
        items.append((sum(ys) / max(len(ys), 1), sum(xs) / max(len(xs), 1), token))
    if not items:
        return ""
    items.sort(key=lambda item: (round(item[0] / _LINE_BUCKET), item[1]))
    lines: list[str] = []
    current_key: int | None = None
    current: list[tuple[float, str]] = []
    for y, x, token in items:
        key = int(round(y / _LINE_BUCKET))
        if current_key is None or key == current_key:
            current.append((x, token))
            current_key = key if current_key is None else current_key
            continue
        current.sort()
        lines.append(" ".join(part for _x, part in current))
        current = [(x, token)]
        current_key = key
    if current:
        current.sort()
        lines.append(" ".join(part for _x, part in current))
    return "\n".join(lines).strip()


def _engine() -> Any:
    global _RAPID_OCR
    if _RAPID_OCR is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as error:
            raise OCRUnavailableError(
                "OCR is unavailable; install the built-in engine with "
                "`pip install rapidocr onnxruntime`."
            ) from error
        _RAPID_OCR = RapidOCR()
    return _RAPID_OCR


def _prepare_image(image: Any) -> Any:
    """Normalize to RGB and upscale very small images for better recognition."""
    from PIL import Image

    if image.mode != "RGB":
        image = image.convert("RGB")
    width, height = image.size
    long_side = max(width, height)
    if long_side < _MIN_LONG_SIDE:
        scale = min(_UPSCALE_FACTOR, _MAX_LONG_SIDE / long_side)
        if scale > 1.05:
            image = image.resize(
                (max(1, int(width * scale)), max(1, int(height * scale))),
                Image.Resampling.LANCZOS,
            )
    return image


def _ocr_with_rapidocr(image: Any) -> str:
    try:
        import numpy as np

        engine = _engine()
    except OCRUnavailableError:
        raise
    except Exception as error:
        raise OCRUnavailableError("The built-in OCR engine could not start.") from error

    result = engine(np.array(image))
    boxed = text_from_boxes(result)
    if boxed:
        return boxed
    texts = getattr(result, "txts", None)
    if texts:
        return "\n".join(str(item).strip() for item in texts if str(item).strip())
    payload = result[0] if isinstance(result, tuple) else result
    if payload is None:
        return ""
    lines: list[str] = []
    for item in payload:
        if isinstance(item, str):
            lines.append(item)
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            text = item[1]
            if isinstance(text, str):
                lines.append(text)
    return "\n".join(line.strip() for line in lines if str(line).strip())


def _tesseract_available() -> bool:
    global _TESSERACT_PROBED, _TESSERACT_AVAILABLE
    if not _TESSERACT_PROBED:
        _TESSERACT_PROBED = True
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            _TESSERACT_AVAILABLE = True
        except Exception:
            _TESSERACT_AVAILABLE = False
    return _TESSERACT_AVAILABLE


def _ocr_with_tesseract(image: Any) -> str:
    """Optional fallback used only when the Tesseract binary really exists."""
    if not _tesseract_available():
        return ""
    try:
        import pytesseract

        return pytesseract.image_to_string(image).strip()
    except Exception as error:
        logger.warning("Tesseract fallback failed: %s", error)
        return ""


def _unlock_pdf(document: object, password: str) -> None:
    needs_pass = bool(
        getattr(document, "needs_pass", False) or getattr(document, "is_encrypted", False)
    )
    if not needs_pass:
        return
    authenticate = getattr(document, "authenticate", None)
    if authenticate is None:
        return
    if password and authenticate(password):
        return
    if authenticate(""):
        return
    raise ValueError(
        "This PDF is password-protected. Provide the document password and retry."
    )