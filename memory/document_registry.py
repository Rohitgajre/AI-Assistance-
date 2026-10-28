"""Document registry for tracking uploaded documents."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class DocumentRecord:
    document_id: str
    filename: str
    file_type: str
    file_size: int
    content_hash: str
    session_id: str | None = None
    user_id: str | None = None
    status: str = "uploaded"  # uploaded, processing, indexed, error
    chunk_count: int = 0
    error_message: str | None = None
    created_at: float = 0
    indexed_at: float | None = None
    metadata: dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at == 0:
            self.created_at = time.time()


class DocumentRegistry:
    def __init__(self, db_path: str = "./askbuddy_memory.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_type TEXT,
                file_size INTEGER,
                content_hash TEXT,
                session_id TEXT,
                user_id TEXT,
                status TEXT,
                chunk_count INTEGER DEFAULT 0,
                error_message TEXT,
                created_at REAL,
                indexed_at REAL,
                metadata TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def register(self, record: DocumentRecord) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO documents 
            (document_id, filename, file_type, file_size, content_hash, session_id, user_id, 
             status, chunk_count, error_message, created_at, indexed_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.document_id, record.filename, record.file_type, record.file_size,
            record.content_hash, record.session_id, record.user_id, record.status,
            record.chunk_count, record.error_message, record.created_at,
            record.indexed_at, json.dumps(record.metadata)
        ))
        conn.commit()
        conn.close()
    
    def get(self, document_id: str) -> DocumentRecord | None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return DocumentRecord(
            document_id=row[0], filename=row[1], file_type=row[2], file_size=row[3],
            content_hash=row[4], session_id=row[5], user_id=row[6], status=row[7],
            chunk_count=row[8], error_message=row[9], created_at=row[10],
            indexed_at=row[11], metadata=json.loads(row[12]) if row[12] else {}
        )
    
    def list_by_session(self, session_id: str) -> list[DocumentRecord]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT * FROM documents WHERE session_id = ? ORDER BY created_at DESC", (session_id,))
        rows = cur.fetchall()
        conn.close()
        results = []
        for row in rows:
            results.append(DocumentRecord(
                document_id=row[0], filename=row[1], file_type=row[2], file_size=row[3],
                content_hash=row[4], session_id=row[5], user_id=row[6], status=row[7],
                chunk_count=row[8], error_message=row[9], created_at=row[10],
                indexed_at=row[11], metadata=json.loads(row[12]) if row[12] else {}
            ))
        return results
    
    def update_status(self, document_id: str, status: str, chunk_count: int = None, error_message: str = None):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        if status == "indexed":
            cur.execute("UPDATE documents SET status = ?, chunk_count = COALESCE(?, chunk_count), indexed_at = ?, error_message = NULL WHERE document_id = ?",
                        (status, chunk_count, time.time(), document_id))
        else:
            cur.execute("UPDATE documents SET status = ?, chunk_count = COALESCE(?, chunk_count), error_message = ? WHERE document_id = ?",
                        (status, chunk_count, error_message, document_id))
        conn.commit()
        conn.close()
    
    def delete(self, document_id: str):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        conn.commit()
        conn.close()


_document_registry = None


def get_document_registry() -> DocumentRegistry:
    global _document_registry
    if _document_registry is None:
        _document_registry = DocumentRegistry()
    return _document_registry
