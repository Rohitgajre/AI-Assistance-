"""Typed schemas for agent tools."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DocumentSearchInput(BaseModel):
    query: str = Field(..., description="Search query")
    document_ids: list[str] = Field(default_factory=list, description="Filter to specific documents")
    top_k: int = Field(default=5, ge=1, le=20)


class DocumentSearchOutput(BaseModel):
    results: list[dict[str, Any]]
    count: int


class StructuredDataQueryInput(BaseModel):
    document_id: str = Field(..., description="Document to query")
    operation: str = Field(..., description="Operation: filter, aggregate, group, compare")
    parameters: dict[str, Any] = Field(default_factory=dict)


class StructuredDataQueryOutput(BaseModel):
    result: Any
    success: bool
    error: str | None = None


class TavilySearchInput(BaseModel):
    query: str = Field(..., description="Search query")
    max_results: int = Field(default=5, ge=1, le=10)


class TavilySearchOutput(BaseModel):
    results: list[dict[str, Any]]
    query: str


class ConversationContextInput(BaseModel):
    query: str = Field(..., description="Query for context")
    conversation_id: str | None = None
    max_messages: int = Field(default=10, ge=1, le=50)


class ConversationContextOutput(BaseModel):
    messages: list[dict[str, Any]]
    context: str
