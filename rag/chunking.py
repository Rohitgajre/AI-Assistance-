"""Structure-aware chunking."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from ingestion.normalization import NormalizedDocument


@dataclass
class Chunk:
    chunk_id: str
    text: str
    document_id: str
    filename: str
    page_number: int | None = None
    section: str | None = None
    element_type: str | None = None
    chunk_index: int = 0
    metadata: dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class StructureAwareChunker:
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def chunk_document(self, doc: NormalizedDocument) -> list[Chunk]:
        chunks = []
        chunk_index = 0
        
        # Try to chunk by elements first (structure-aware)
        for elem in doc.elements:
            text = elem.text.strip()
            if not text:
                continue
            
            # If element is small enough, keep as one chunk
            if len(text) <= self.chunk_size:
                chunks.append(Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=text,
                    document_id=doc.metadata.document_id,
                    filename=doc.metadata.filename,
                    page_number=elem.page_number,
                    section=elem.section,
                    element_type=elem.element_type,
                    chunk_index=chunk_index,
                    metadata={"content_hash": doc.metadata.content_hash}
                ))
                chunk_index += 1
            else:
                # Split large elements with overlap
                start = 0
                while start < len(text):
                    end = start + self.chunk_size
                    chunk_text = text[start:end]
                    
                    # Try to break at sentence/paragraph boundary
                    if end < len(text):
                        # Look for good break points
                        for sep in ['\n\n', '. ', '\n', ' ']:
                            last_sep = chunk_text.rfind(sep)
                            if last_sep > self.chunk_size * 0.5:
                                chunk_text = chunk_text[:last_sep + len(sep)]
                                end = start + len(chunk_text)
                                break
                    
                    chunks.append(Chunk(
                        chunk_id=str(uuid.uuid4()),
                        text=chunk_text.strip(),
                        document_id=doc.metadata.document_id,
                        filename=doc.metadata.filename,
                        page_number=elem.page_number,
                        section=elem.section,
                        element_type=elem.element_type,
                        chunk_index=chunk_index,
                        metadata={"content_hash": doc.metadata.content_hash}
                    ))
                    chunk_index += 1
                    start = end - self.chunk_overlap
                    if start < 0:
                        start = 0
        
        # If no elements were chunked, fall back to text chunking
        if not chunks and doc.text:
            text = doc.text
            start = 0
            while start < len(text):
                end = start + self.chunk_size
                chunk_text = text[start:end]
                if end < len(text):
                    for sep in ['\n\n', '. ', '\n', ' ']:
                        last_sep = chunk_text.rfind(sep)
                        if last_sep > self.chunk_size * 0.5:
                            chunk_text = chunk_text[:last_sep + len(sep)]
                            end = start + len(chunk_text)
                            break
                chunks.append(Chunk(
                    chunk_id=str(uuid.uuid4()),
                    text=chunk_text.strip(),
                    document_id=doc.metadata.document_id,
                    filename=doc.metadata.filename,
                    page_number=None,
                    section=None,
                    element_type='text',
                    chunk_index=chunk_index,
                    metadata={"content_hash": doc.metadata.content_hash}
                ))
                chunk_index += 1
                start = end - self.chunk_overlap
                if start < 0:
                    start = 0
        
        return chunks
