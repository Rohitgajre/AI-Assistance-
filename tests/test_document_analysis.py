"""Offline tests for isolated document ingestion and Ollama behavior."""

import json

import pytest

from document_analysis.config import OllamaSettings
from document_analysis.exceptions import InvalidDocumentError, OllamaUnavailableError
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
