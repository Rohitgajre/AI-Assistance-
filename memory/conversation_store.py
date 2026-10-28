"""Persistent conversation history with SQLite."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Message:
    message_id: str
    conversation_id: str
    role: str  # user, assistant, system
    content: str
    metadata: dict[str, Any] = None
    created_at: float = 0
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at == 0:
            self.created_at = time.time()


@dataclass
class Conversation:
    conversation_id: str
    session_id: str | None = None
    user_id: str | None = None
    title: str = ""
    created_at: float = 0
    updated_at: float = 0
    metadata: dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_at == 0:
            self.created_at = time.time()
        if self.updated_at == 0:
            self.updated_at = self.created_at


class ConversationStore:
    def __init__(self, db_path: str = "./askbuddy_memory.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                session_id TEXT,
                user_id TEXT,
                title TEXT,
                created_at REAL,
                updated_at REAL,
                metadata TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at REAL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id)")
        conn.commit()
        conn.close()
    
    def create_conversation(self, conversation: Conversation) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO conversations 
            (conversation_id, session_id, user_id, title, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            conversation.conversation_id, conversation.session_id, conversation.user_id,
            conversation.title, conversation.created_at, conversation.updated_at,
            json.dumps(conversation.metadata)
        ))
        conn.commit()
        conn.close()
    
    def add_message(self, message: Message) -> None:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO messages 
            (message_id, conversation_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            message.message_id, message.conversation_id, message.role,
            message.content, json.dumps(message.metadata), message.created_at
        ))
        # Update conversation timestamp
        cur.execute("UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (time.time(), message.conversation_id))
        conn.commit()
        conn.close()
    
    def get_conversation_messages(self, conversation_id: str, limit: int = 50) -> list[Message]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            SELECT message_id, conversation_id, role, content, metadata, created_at
            FROM messages WHERE conversation_id = ? 
            ORDER BY created_at ASC LIMIT ?
        """, (conversation_id, limit))
        rows = cur.fetchall()
        conn.close()
        results = []
        for row in rows:
            results.append(Message(
                message_id=row[0], conversation_id=row[1], role=row[2],
                content=row[3], metadata=json.loads(row[4]) if row[4] else {},
                created_at=row[5]
            ))
        return results
    
    def list_conversations(self, session_id: str = None, user_id: str = None) -> list[Conversation]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        if session_id:
            cur.execute("SELECT * FROM conversations WHERE session_id = ? ORDER BY updated_at DESC", (session_id,))
        elif user_id:
            cur.execute("SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC", (user_id,))
        else:
            cur.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 50")
        rows = cur.fetchall()
        conn.close()
        results = []
        for row in rows:
            results.append(Conversation(
                conversation_id=row[0], session_id=row[1], user_id=row[2],
                title=row[3], created_at=row[4], updated_at=row[5],
                metadata=json.loads(row[6]) if row[6] else {}
            ))
        return results
    
    def delete_conversation(self, conversation_id: str):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cur.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
        conn.commit()
        conn.close()


_conversation_store = None


def get_conversation_store() -> ConversationStore:
    global _conversation_store
    if _conversation_store is None:
        _conversation_store = ConversationStore()
    return _conversation_store
