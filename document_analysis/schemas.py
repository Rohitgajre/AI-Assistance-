"""Validated domain objects for document analysis."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    UPLOADED = "uploaded"
    COMPLETED = "completed"
    FAILED = "failed"


class SourceReference(BaseModel):
    section: str
    excerpt: str = Field(max_length=300)


class DocumentChunk(BaseModel):
    text: str
    source: SourceReference


class DocumentRecord(BaseModel):
    document_id: str
    filename: str
    document_type: str
    status: DocumentStatus = DocumentStatus.UPLOADED
    chunks: list[DocumentChunk]
    error: str | None = None


class DocumentAnalysis(BaseModel):
    document_type: str
    executive_summary: str
    detailed_summary: str
    key_findings: list[str] = Field(default_factory=list)
    important_entities: list[str] = Field(default_factory=list)
    dates_and_amounts: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    classification: str
    source_references: list[SourceReference] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    document_id: str
    status: DocumentStatus
    analysis: DocumentAnalysis
    model: str
    processing_time_seconds: float


class AnswerResult(BaseModel):
    document_id: str
    answer: str
    source_references: list[SourceReference]
    model: str
