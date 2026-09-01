"""SQLite-backed conversation memory for the documentation assistant."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class ConversationAccessError(ValueError):
    """The supplied client does not own the conversation."""


class ConversationNotFoundError(ValueError):
    """The requested conversation does not exist."""


class ConversationStore:
    """Persist conversation turns in a small, single-file SQLite database.

    The store opens a short-lived connection for each operation. This keeps the
    service safe when FastAPI runs synchronous handlers in its thread pool and
    avoids sharing a SQLite connection across threads. A per-conversation lock
    prevents two requests for the same conversation from reading the same
    history and then appending out of order.
    """

    def __init__(
        self,
        path: str,
        *,
        ttl_seconds: int = 30 * 24 * 60 * 60,
        max_messages: int = 24,
    ) -> None:
        if not path.strip():
            raise ValueError("DOCS_ASSISTANT_DB_PATH must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("memory TTL must be positive")
        if max_messages <= 0:
            raise ValueError("memory message limit must be positive")

        self.path = Path(path).expanduser()
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages
        self._schema_lock = threading.Lock()
        self._locks_lock = threading.Lock()
        self._conversation_locks: dict[str, threading.Lock] = {}
        self._schema_ready = False
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        conversation_id TEXT PRIMARY KEY,
                        client_id TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                        content TEXT NOT NULL,
                        sources_json TEXT NOT NULL DEFAULT '[]',
                        created_at REAL NOT NULL,
                        FOREIGN KEY (conversation_id)
                            REFERENCES conversations (conversation_id)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_messages_conversation
                        ON messages (conversation_id, id);
                    CREATE INDEX IF NOT EXISTS idx_conversations_updated
                        ON conversations (updated_at);
                    """
                )
            self._schema_ready = True

    @contextmanager
    def conversation_lock(self, conversation_id: str) -> Iterator[None]:
        """Serialize requests that target one conversation."""

        with self._locks_lock:
            lock = self._conversation_locks.setdefault(
                conversation_id, threading.Lock()
            )
        with lock:
            yield

    def _prune_expired(self, connection: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.ttl_seconds
        connection.execute(
            "DELETE FROM conversations WHERE updated_at < ?",
            (cutoff,),
        )

    def ensure(self, conversation_id: str, client_id: str) -> None:
        now = time.time()
        with self._connect() as connection:
            self._prune_expired(connection, now)
            row = connection.execute(
                "SELECT client_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO conversations
                        (conversation_id, client_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, client_id, now, now),
                )
                return
            if row["client_id"] != client_id:
                raise ConversationAccessError("conversation does not belong to client")

    def load(
        self,
        conversation_id: str,
        client_id: str,
        *,
        create: bool = False,
    ) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as connection:
            self._prune_expired(connection, now)
            row = connection.execute(
                "SELECT client_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                if not create:
                    raise ConversationNotFoundError("conversation was not found")
                connection.execute(
                    """
                    INSERT INTO conversations
                        (conversation_id, client_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (conversation_id, client_id, now, now),
                )
            elif row["client_id"] != client_id:
                raise ConversationAccessError("conversation does not belong to client")

            rows = connection.execute(
                """
                SELECT role, content, sources_json
                FROM messages
                WHERE conversation_id = ?
                  AND id IN (
                      SELECT id
                      FROM messages
                      WHERE conversation_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                ORDER BY id ASC
                """,
                (conversation_id, conversation_id, self.max_messages),
            ).fetchall()

        messages: list[dict[str, Any]] = []
        for row in rows:
            try:
                sources = json.loads(row["sources_json"])
            except json.JSONDecodeError:
                sources = []
            messages.append(
                {
                    "role": row["role"],
                    "content": row["content"],
                    "sources": sources if isinstance(sources, list) else [],
                }
            )
        return messages

    def append(
        self,
        conversation_id: str,
        client_id: str,
        *,
        role: str,
        content: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("message role must be user or assistant")
        if not content.strip():
            raise ValueError("message content must not be empty")

        now = time.time()
        sources_json = json.dumps(sources or [], ensure_ascii=False)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT client_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ConversationNotFoundError("conversation was not found")
            if row["client_id"] != client_id:
                raise ConversationAccessError("conversation does not belong to client")
            connection.execute(
                """
                INSERT INTO messages
                    (conversation_id, role, content, sources_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, sources_json, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now, conversation_id),
            )
            connection.execute(
                """
                DELETE FROM messages
                WHERE conversation_id = ?
                  AND id NOT IN (
                      SELECT id
                      FROM messages
                      WHERE conversation_id = ?
                      ORDER BY id DESC
                      LIMIT ?
                  )
                """,
                (conversation_id, conversation_id, self.max_messages),
            )

    def delete(self, conversation_id: str, client_id: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT client_id FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                raise ConversationNotFoundError("conversation was not found")
            if row["client_id"] != client_id:
                raise ConversationAccessError("conversation does not belong to client")
            connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
