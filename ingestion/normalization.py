"""Common internal document representation and normalization."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocumentMetadata:
    document_id: str
    filename: str
    file_type: str
    mime_type: str | None = None
    content_hash: str = ""
    page_count: int | None = None
    upload_session_id: str | None = None
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentElement:
    element_id: str
    text: str
    element_type: str  # paragraph, heading, table, list, image_caption, etc.
    page_number: int | None = None
    section: str | None = None
    bbox: dict[str, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedDocument:
    metadata: DocumentMetadata
    elements: list[DocumentElement]
    text: str  # full concatenated text
    markdown: str | None = None
    tables: list[dict[str, Any]] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)


def compute_content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
