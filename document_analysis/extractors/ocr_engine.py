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
import math
from typing import Any

logger = logging.getLogger(__name__)

# Hardcoded engine tuning. Not environment-driven: the OCR stack is meant to
# behave identically everywhere without configuration.
_MIN_RENDER_ZOOM = 2.0  # floor for pymupdf rasterization of scanned PDF pages
_MAX_RENDER_ZOOM = 5.0  # ceiling: beyond this there is nothing left to resolve
_MAX_RENDER_PIXELS = 12_000_000  # per-page pixel budget (keeps memory bounded)
_LINE_MERGE_RATIO = 0.4  # line tolerance as a fraction of the median glyph height
_LINE_MIN_TOLERANCE = 3.0  # px; absorbs the 1-2px drift inside a printed row
_LINE_MAX_TOLERANCE = 12.0
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
    """Render every PDF page to a PIL image; public so callers can inspect pages.

    Scanned pages are rasterized at the *native* resolution of the embedded
    scan instead of an arbitrary fixed zoom. A 300 DPI letter page is stored as
    ~2550x3300 pixels, so a 2x zoom would hand the recognizer a downscaled,
    blurrier image and silently lose thin glyphs (decimal points, asterisks).
    The zoom is bounded below (so thin vector pages still get upscaled) and by a
    total pixel budget (so huge pages cannot blow up memory).
    """
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
        zoom = _render_zoom(page)
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        from PIL import Image

        images.append(Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples))
    if not images:
        raise ValueError("The PDF has no pages to read.")
    return images


def _render_zoom(page: object) -> float:
    """Zoom that renders a scanned page at (at most) its embedded image DPI."""
    try:
        width_pt = float(getattr(getattr(page, "rect", None), "width", 0.0) or 0.0)
        height_pt = float(getattr(getattr(page, "rect", None), "height", 0.0) or 0.0)
    except (TypeError, ValueError):
        return _MIN_RENDER_ZOOM
    if width_pt <= 0 or height_pt <= 0:
        return _MIN_RENDER_ZOOM

    zoom = _MIN_RENDER_ZOOM
    for pixels, placed in _page_image_geometry(page, width_pt):
        if pixels > 0 and placed > 0:
            zoom = max(zoom, pixels / placed)
    zoom = min(zoom, _MAX_RENDER_ZOOM)

    budget = math.sqrt(_MAX_RENDER_PIXELS / (width_pt * height_pt))
    return max(min(zoom, budget), _MIN_RENDER_ZOOM)


def _page_image_geometry(page: object, width_pt: float) -> list[tuple[int, float]]:
    """Return ``(pixel_width, placed_width_pt)`` for every image on the page."""
    geometry: list[tuple[int, float]] = []
    try:
        infos = page.get_image_info()  # pymupdf >= 1.19
    except Exception:
        infos = []
    for info in infos or []:
        try:
            pixels = int(info.get("width", 0))
            bbox = info.get("bbox") or ()
            placed = float(bbox[2]) - float(bbox[0])
        except (AttributeError, IndexError, TypeError, ValueError):
            continue
        if pixels > 0 and placed > 0:
            geometry.append((pixels, placed))
    if geometry:
        return geometry

    # Older pymupdf: assume each image covers the whole page.
    try:
        for image in page.get_images(full=True) or []:
            pixels = int(image[2])
            if pixels > 0 and width_pt > 0:
                geometry.append((pixels, width_pt))
    except Exception:
        return []
    return geometry


def _tokens_from_result(result: object) -> list[dict[str, float | str]]:
    """Normalize a RapidOCR result into token dicts with box coordinates.

    Each token keeps ``text`` plus its bounding box (``x0``/``x1``/``y0``/``y1``)
    and center (``cx``/``cy``) so downstream consumers can reconstruct lines,
    detect column boundaries, or re-sort tokens without re-running OCR.
    """
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    if boxes is None or texts is None:
        return []
    tokens: list[dict[str, float | str]] = []
    for box, text in zip(boxes, texts, strict=False):
        token = str(text).strip()
        if not token:
            continue
        try:
            points = list(box)
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
        except Exception:
            continue
        tokens.append(
            {
                "text": token,
                "x0": min(xs),
                "x1": max(xs),
                "y0": min(ys),
                "y1": max(ys),
                "cx": sum(xs) / max(len(xs), 1),
                "cy": sum(ys) / max(len(ys), 1),
            }
        )
    return tokens


def text_from_boxes(result: object) -> str:
    """Reconstruct readable lines from RapidOCR box results.

    Tokens are grouped by vertical position (``_LINE_BUCKET`` tolerance) and
    sorted left-to-right inside each line, which preserves the reading order
    that matters for tables such as bank statements.
    """
    lines = lines_from_result(result)
    return "\n".join(" ".join(str(token["text"]) for token in line) for line in lines).strip()


def lines_from_result(result: object) -> list[list[dict[str, float | str]]]:
    """Group OCR tokens into reading-order lines, keeping per-token coordinates.

    Returns a list of lines; every line is a list of token dicts (see
    ``_tokens_from_result``) already sorted left-to-right. Consumers that need
    column-aware table extraction can use the ``cx`` values directly.

    Tokens are merged while their vertical centres stay within a tolerance
    derived from the median glyph height. Fixed pixel buckets were not good
    enough: a printed row whose cells differ by a single pixel (very common
    after a 300 DPI fax scan) straddled a bucket edge and was torn into two
    "lines", which then read as a transaction with no description and no
    balance. The tolerance now scales with the text, so the drift is absorbed
    while genuinely different rows (a full line pitch apart) stay apart.
    """
    tokens = _tokens_from_result(result)
    if not tokens:
        return []
    tolerance = _line_tolerance(tokens)
    tokens.sort(key=lambda item: (float(item["cy"]), float(item["cx"])))

    lines: list[list[dict[str, float | str]]] = []
    centers: list[float] = []
    for token in tokens:
        center = float(token["cy"])
        if lines and abs(center - centers[-1]) <= tolerance:
            lines[-1].append(token)
            count = len(lines[-1])
            centers[-1] += (center - centers[-1]) / count
            continue
        lines.append([token])
        centers.append(center)
    for line in lines:
        line.sort(key=lambda item: float(item["cx"]))
    return lines


def _line_tolerance(tokens: list[dict[str, float | str]]) -> float:
    """Vertical merge tolerance (px) for one page, scaled to its glyph size."""
    heights = sorted(
        float(token["y1"]) - float(token["y0"])
        for token in tokens
        if float(token.get("y1", 0.0)) > float(token.get("y0", 0.0))
    )
    if not heights:
        return _LINE_MAX_TOLERANCE
    median = heights[len(heights) // 2]
    return min(_LINE_MAX_TOLERANCE, max(_LINE_MIN_TOLERANCE, _LINE_MERGE_RATIO * median))


def ocr_image_lines(image: Any) -> list[list[dict[str, float | str]]]:
    """OCR an image and return per-line token records (coordinates preserved).

    Returns ``[]`` when the backend produced no boxed text; callers then fall
    back to the plain-text path.
    """
    prepared = _prepare_image(image)
    try:
        import numpy as np

        engine = _engine()
    except OCRUnavailableError:
        raise
    except Exception as error:
        raise OCRUnavailableError("The built-in OCR engine could not start.") from error
    result = engine(np.array(prepared))
    return lines_from_result(result)


def ocr_pdf_lines(
    pdf_bytes: bytes,
    password: str = "",
) -> list[list[list[dict[str, float | str]]]]:
    """OCR every page and return per-page line records with coordinates."""
    from PIL import Image

    pages: list[list[list[dict[str, float | str]]]] = []
    for image in rasterize_pdf(pdf_bytes, password):
        if isinstance(image, Image.Image):
            lines = ocr_image_lines(image)
            if lines:
                pages.append(lines)
    return pages


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