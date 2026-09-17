"""In-memory ingestion, retrieval, and isolated Ollama integration."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from document_analysis.config import OllamaSettings
from document_analysis.exceptions import (
    InvalidDocumentError,
    InvalidModelResponseError,
    OllamaUnavailableError,
)
from document_analysis.extractors import extract_document
from document_analysis.schemas import (
    AnalysisResult,
    AnswerResult,
    DocumentAnalysis,
    DocumentChunk,
    DocumentRecord,
    DocumentStatus,
    SourceReference,
)

PROMPT_DIR = Path(__file__).parent / "prompts"


class OllamaClient:
    """Minimal Ollama HTTP client with no impact on Gemini's configuration."""

    def __init__(self, settings: OllamaSettings) -> None:
        self.settings = settings

    def health(self) -> bool:
        try:
            self._request("/api/tags", None)
        except OllamaUnavailableError:
            return False
        return True

    def generate(self, prompt: str, *, json_mode: bool = False) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 700},
        }
        if json_mode:
            payload["format"] = "json"
        response = self._request("/api/generate", payload)
        answer = response.get("response")
        if not isinstance(answer, str) or not answer.strip():
            raise InvalidModelResponseError("Ollama returned no text response.")
        return answer.strip()

    def _request(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.settings.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:  # nosec B310 - configured local Ollama URL
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OllamaUnavailableError(
                "Ollama is unavailable. Start Ollama and verify its base URL and model."
            ) from error
        if not isinstance(body, dict):
            raise InvalidModelResponseError("Ollama returned an invalid response.")
        return body


class DocumentAnalysisService:
    """Session-safe service: only extracted text is retained by the caller."""

    def __init__(self, client: OllamaClient, settings: OllamaSettings) -> None:
        self.client = client
        self.settings = settings

    def ingest(self, filename: str, content: bytes) -> DocumentRecord:
        if not filename or len(content) > self.settings.max_upload_bytes:
            raise InvalidDocumentError("Upload is missing or exceeds the 20 MB limit.")
        chunks = self._chunk(extract_document(filename, content))
        return DocumentRecord(
            document_id=str(uuid4()),
            filename=Path(filename).name,
            document_type=filename.rsplit(".", 1)[-1].lower(),
            chunks=chunks,
        )

    def analyze(self, document: DocumentRecord) -> AnalysisResult:
        started = time.perf_counter()
        prompt = (
            self._read_prompt("analysis_prompt.txt")
            + "\n\nDOCUMENT:\n"
            + self._context(document.chunks)
        )
        try:
            analysis = _parse_analysis(self.client.generate(prompt, json_mode=True))
        except ValueError as error:
            raise InvalidModelResponseError(
                "Ollama did not return a valid analysis JSON object."
            ) from error
        analysis.source_references = [chunk.source for chunk in document.chunks[:8]]
        document.status = DocumentStatus.COMPLETED
        return AnalysisResult(
            document_id=document.document_id,
            status=document.status,
            analysis=analysis,
            model=self.settings.model,
            processing_time_seconds=round(time.perf_counter() - started, 3),
        )

    def ask(self, document: DocumentRecord, question: str) -> AnswerResult:
        selected = self._retrieve(document.chunks, question)
        prompt = (
            self._read_prompt("qa_prompt.txt")
            + f"\n\nQUESTION: {question}\n\nDOCUMENT:\n"
            + self._context(selected)
        )
        return AnswerResult(
            document_id=document.document_id,
            answer=self.client.generate(prompt),
            source_references=[chunk.source for chunk in selected],
            model=self.settings.model,
        )

    def _chunk(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        output = []
        for chunk in chunks:
            text = chunk.text
            for start in range(
                0, len(text), self.settings.chunk_size - self.settings.chunk_overlap
            ):
                part = text[start : start + self.settings.chunk_size].strip()
                if part:
                    output.append(
                        DocumentChunk(
                            text=part,
                            source=SourceReference(
                                section=chunk.source.section, excerpt=part[:300]
                            ),
                        )
                    )
        if not output:
            raise InvalidDocumentError("The uploaded document is empty.")
        return output

    def _retrieve(self, chunks: list[DocumentChunk], question: str) -> list[DocumentChunk]:
        terms = {term.lower() for term in question.split() if len(term) > 2}
        return sorted(
            chunks, key=lambda item: sum(term in item.text.lower() for term in terms), reverse=True
        )[:4]

    def _context(self, chunks: list[DocumentChunk]) -> str:
        return "\n\n".join(f"[{chunk.source.section}]\n{chunk.text}" for chunk in chunks)[:12_000]

    @staticmethod
    def _read_prompt(name: str) -> str:
        return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _parse_analysis(response: str) -> DocumentAnalysis:
    """Validate JSON even when a small model wraps it in Markdown prose."""
    start = response.find("{")
    if start < 0:
        raise InvalidModelResponseError("Ollama did not return a JSON object for the analysis.")

    try:
        payload, _ = json.JSONDecoder().raw_decode(response[start:])
    except json.JSONDecodeError as error:
        raise InvalidModelResponseError(
            "Ollama did not return a valid analysis JSON object."
        ) from error
    if not isinstance(payload, Mapping):
        raise InvalidModelResponseError("Ollama analysis must be a JSON object.")

    missing = "Information not found in the uploaded document."
    defaults: dict[str, Any] = {
        "document_type": "unknown",
        "executive_summary": missing,
        "detailed_summary": missing,
        "key_findings": [],
        "important_entities": [],
        "dates_and_amounts": [],
        "action_items": [],
        "risks": [],
        "classification": "unknown",
    }
    try:
        return DocumentAnalysis.model_validate({**defaults, **payload})
    except ValueError as error:
        raise InvalidModelResponseError(
            "Ollama returned analysis fields with an invalid format."
        ) from error
