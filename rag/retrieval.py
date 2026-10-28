"""Retrieval logic for RAG."""
from __future__ import annotations

import logging
from typing import Any

from providers.embedding_provider import get_embedding_provider
from rag.vector_store import get_vector_store

logger = logging.getLogger(__name__)


class Retriever:
    def __init__(self):
        self.embedding_provider = get_embedding_provider()
        self.vector_store = get_vector_store()
    
    def retrieve(self, query: str, top_k: int = 5, 
                 document_ids: list[str] | None = None,
                 session_id: str | None = None,
                 min_score: float = 0.0) -> list[dict[str, Any]]:
        try:
            embeddings = self.embedding_provider.get_embeddings()
            query_embedding = embeddings.embed_query(query)
            
            results = self.vector_store.search(
                query_embedding=query_embedding,
                top_k=top_k,
                document_ids=document_ids,
                session_id=session_id
            )
            
            # Filter by score if needed (Chroma uses distance, lower is better for cosine)
            filtered = []
            for r in results:
                dist = r.get('distance')
                if dist is not None and min_score > 0:
                    # Convert distance to similarity roughly
                    similarity = 1 - dist if dist <= 2 else 0
                    if similarity < min_score:
                        continue
                filtered.append(r)
            
            return filtered
        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            return []
    
    def format_context(self, results: list[dict[str, Any]], max_chars: int = 12000) -> str:
        if not results:
            return ""
        
        parts = []
        for r in results:
            meta = r.get('metadata', {})
            filename = meta.get('filename', 'unknown')
            page = meta.get('page_number')
            section = meta.get('section')
            
            source_info = f"[Source: {filename}"
            if page is not None:
                source_info += f", Page {page}"
            if section:
                source_info += f", Section: {section}"
            source_info += "]"
            
            parts.append(f"{source_info}\n{r['text']}")
        
        context = "\n\n---\n\n".join(parts)
        if len(context) > max_chars:
            context = context[:max_chars] + "\n\n[... context truncated ...]"
        return context


_retriever = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever
