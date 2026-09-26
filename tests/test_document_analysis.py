"""Offline tests for isolated document ingestion and Ollama behavior."""

import json
from io import BytesIO

import pytest
from PIL import Image

from document_analysis.config import OllamaSettings
from document_analysis.exceptions import InvalidDocumentError, OllamaUnavailableError
from document_analysis.extractors.image_extractor import extract_image
from document_analysis.extractors.ocr_engine import lines_from_result, text_from_boxes
from document_analysis.extractors.pdf_extractor import extract_pdf
from document_analysis.service import DocumentAnalysisService


class FakeOllamaClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.prompts: list[str] = []

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise OllamaUnavailableError("Ollama is unavailable")
        return self.responses.pop(0)


def make_service(
    responses: list[str] | None = None,
) -> tuple[DocumentAnalysisService, FakeOllamaClient]:
    client = FakeOllamaClient(responses)
    return DocumentAnalysisService(client, OllamaSettings()), client


def test_ingest_txt_generates_a_safe_id_and_source_reference() -> None:
    service, _ = make_service()

    document = service.ingest("notes.txt", b"Revenue increased in April.")

    assert document.filename == "notes.txt"
    assert document.chunks[0].source.section == "document"
    assert document.chunks[0].text == "Revenue increased in April."


def test_ingest_rejects_unsupported_or_empty_uploads() -> None:
    service, _ = make_service()

    with pytest.raises(InvalidDocumentError, match="Unsupported"):
        service.ingest("malware.exe", b"not executable")
    with pytest.raises(InvalidDocumentError, match="empty"):
        service.ingest("empty.txt", b"   ")


def test_analysis_is_validated_and_keeps_source_references() -> None:
    response = json.dumps(
        {
            "document_type": "report",
            "executive_summary": "Revenue increased in April.",
            "detailed_summary": "The document reports an April revenue increase.",
            "key_findings": ["Revenue increased."],
            "important_entities": [],
            "dates_and_amounts": ["April"],
            "action_items": [],
            "risks": [],
            "classification": "business report",
        }
    )
    service, client = make_service([response])
    document = service.ingest("report.txt", b"Revenue increased in April.")

    result = service.analyze(document)

    assert result.status.value == "completed"
    assert result.analysis.source_references[0].section == "document"
    assert "Revenue increased" in client.prompts[0]


def test_analysis_accepts_fenced_json_and_defaults_missing_fields() -> None:
    response = "```json\n{\"executive_summary\": \"Revenue increased.\"}\n```"
    service, _ = make_service([response])
    document = service.ingest("report.txt", b"Revenue increased in April.")

    result = service.analyze(document)

    assert result.analysis.executive_summary == "Revenue increased."
    assert result.analysis.detailed_summary.startswith("Information not found")


def test_question_uses_document_context_and_preserves_sources() -> None:
    service, client = make_service(["Revenue increased in April."])
    document = service.ingest("report.txt", b"Revenue increased in April.")

    answer = service.ask(document, "When did revenue increase?")

    assert answer.answer == "Revenue increased in April."
    assert answer.source_references[0].section == "document"
    assert "Information not found" in client.prompts[0]


def test_ollama_unavailable_does_not_change_document_state() -> None:
    service, _ = make_service()
    document = service.ingest("report.txt", b"Revenue increased in April.")

    with pytest.raises(OllamaUnavailableError):
        service.analyze(document)
    assert document.status.value == "uploaded"


class _BoxResult:
    def __init__(self, boxes: list[object], txts: list[str]) -> None:
        self.boxes = boxes
        self.txts = txts


def test_text_from_boxes_reconstructs_reading_order() -> None:
    result = _BoxResult(
        boxes=[
            [[210, 10], [400, 10], [400, 30], [210, 30]],  # "World" (rightmost)
            [[10, 10], [200, 10], [200, 30], [10, 30]],  # "Hello" (leftmost)
        ],
        txts=["World", "Hello"],
    )

    assert text_from_boxes(result) == "Hello World"


def test_lines_from_result_merges_drifted_cells_but_keeps_rows_apart() -> None:
    """A printed row whose cells drift a couple of px must stay one line.

    300 DPI scans put a row's cells at slightly different baselines. With fixed
    pixel buckets such a row straddled the bucket edge and was torn in two,
    which the statement pipeline then read as a row with no description and no
    balance. The merge tolerance scales with the glyph height instead.
    """

    def _box(x0: float, y0: float, y1: float) -> list[list[float]]:
        return [[x0, y0], [x0 + 60, y0], [x0 + 60, y1], [x0, y1]]

    # Row 1 cells drift 2px around y=100; row 2 sits a full line pitch below.
    result = _BoxResult(
        boxes=[
            _box(10, 90, 108),  # 10/05  (y 90-108)
            _box(120, 92, 110),  # CHECK 1234 (drifts 2px lower)
            _box(400, 91, 109),  # 9.98
            _box(500, 92, 110),  # 807.08
            _box(10, 130, 148),  # 10/06 (next row, far enough to stay apart)
            _box(120, 131, 149),  # POS PURCHASE
        ],
        txts=["10/05", "CHECK 1234", "9.98", "807.08", "10/06", "POS PURCHASE"],
    )

    lines = lines_from_result(result)

    assert [[str(token["text"]) for token in line] for line in lines] == [
        ["10/05", "CHECK 1234", "9.98", "807.08"],
        ["10/06", "POS PURCHASE"],
    ]
    # Coordinates survive so the column-aware extractor can read the amounts.
    assert [round(float(line[-1]["cx"])) for line in lines] == [530, 150]


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (120, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_extract_image_uses_builtin_ocr_engine(monkeypatch) -> None:
    calls: list[Image.Image] = []
    monkeypatch.setattr(
        "document_analysis.extractors.image_extractor.ocr_image",
        lambda image: calls.append(image) or "Statement text",
    )

    chunks = extract_image(_png_bytes())

    assert len(calls) == 1
    assert chunks[0].source.section == "OCR"
    assert chunks[0].text == "Statement text"


def _blank_pdf_bytes() -> bytes:
    import pymupdf

    document = pymupdf.open()
    document.new_page(width=595, height=842)
    return document.tobytes()


def test_extract_pdf_falls_back_to_ocr_when_scanned(monkeypatch) -> None:
    monkeypatch.setattr(
        "document_analysis.extractors.pdf_extractor.ocr_pdf",
        lambda content: ["Statement page one", "Statement page two"],
    )

    chunks = extract_pdf(_blank_pdf_bytes())

    assert [chunk.source.section for chunk in chunks] == ["page 1 (OCR)", "page 2 (OCR)"]
    assert chunks[0].text == "Statement page one"


def test_extract_pdf_reports_error_when_ocr_finds_no_text(monkeypatch) -> None:
    def unavailable(content: bytes) -> list[str]:
        raise ValueError("No readable text was found in the scanned PDF.")

    monkeypatch.setattr("document_analysis.extractors.pdf_extractor.ocr_pdf", unavailable)

    with pytest.raises(InvalidDocumentError, match="No readable text") as excinfo:
        extract_pdf(_blank_pdf_bytes())
    assert "scanned PDF" in str(excinfo.value)
