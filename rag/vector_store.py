"""Vector store interface and Chroma implementation."""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from typing import Any

from rag.chunking import Chunk

logger = logging.getLogger(__name__)


class VectorStoreInterface(ABC):
    @abstractmethod
    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        pass
    
    @abstractmethod
    def search(self, query_embedding: list[float], top_k: int = 5, 
               document_ids: list[str] | None = None, 
               session_id: str | None = None) -> list[dict[str, Any]]:
        pass
    
    @abstractmethod
    def delete_document(self, document_id: str) -> None:
        pass
    
    @abstractmethod
    def get_document_count(self, document_id: str) -> int:
        pass


class ChromaVectorStore(VectorStoreInterface):
    def __init__(self, persist_directory: str = "./chroma_db", collection_name: str = "askbuddy_docs"):
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._client = None
        self._collection = None
    
    def _get_collection(self):
        if self._collection is None:
            try:
                import chromadb
                from chromadb.config import Settings
                
                os.makedirs(self.persist_directory, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=Settings(anonymized_telemetry=False)
                )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"}
                )
                logger.info(f"Chroma collection '{self.collection_name}' ready")
            except Exception as e:
                logger.error(f"Failed to initialize Chroma: {e}")
                raise
        return self._collection
    
    def add_chunks(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        if not chunks or not embeddings:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("Chunks and embeddings must have same length")
        
        collection = self._get_collection()
        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas = []
        for chunk in chunks:
            meta = {
                "document_id": chunk.document_id,
                "filename": chunk.filename,
                "chunk_index": chunk.chunk_index,
            }
            if chunk.page_number is not None:
                meta["page_number"] = chunk.page_number
            if chunk.section:
                meta["section"] = chunk.section
            if chunk.element_type:
                meta["element_type"] = chunk.element_type
            if chunk.metadata:
                for k, v in chunk.metadata.items():
                    if isinstance(v, (str, int, float, bool)):
                        meta[k] = v
            metadatas.append(meta)
        
        collection.add(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas
        )
        logger.info(f"Added {len(chunks)} chunks to vector store")
    
    def search(self, query_embedding: list[float], top_k: int = 5,
               document_ids: list[str] | None = None,
               session_id: str | None = None) -> list[dict[str, Any]]:
        collection = self._get_collection()
        
        where = None
        if document_ids:
            if len(document_ids) == 1:
                where = {"document_id": document_ids[0]}
            else:
                where = {"document_id": {"$in": document_ids}}
        
        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"]
            )
            
            items = []
            if results['ids'] and results['ids'][0]:
                for i, doc_id in enumerate(results['ids'][0]):
                    items.append({
                        "id": doc_id,
                        "text": results['documents'][0][i] if results['documents'] else "",
                        "metadata": results['metadatas'][0][i] if results['metadatas'] else {},
                        "distance": results['distances'][0][i] if results['distances'] else None,
                    })
            return items
        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return []
    
    def delete_document(self, document_id: str) -> None:
        collection = self._get_collection()
        try:
            # Find all ids for this document
            results = collection.get(where={"document_id": document_id})
            if results['ids']:
                collection.delete(ids=results['ids'])
                logger.info(f"Deleted {len(results['ids'])} chunks for document {document_id}")
        except Exception as e:
            logger.error(f"Failed to delete document {document_id}: {e}")
    
    def get_document_count(self, document_id: str) -> int:
        collection = self._get_collection()
        try:
            results = collection.get(where={"document_id": document_id})
            return len(results['ids']) if results['ids'] else 0
        except Exception:
            return 0


_vector_store = None


def get_vector_store() -> VectorStoreInterface:
    global _vector_store
    if _vector_store is None:
        _vector_store = ChromaVectorStore()
    return _vector_store
