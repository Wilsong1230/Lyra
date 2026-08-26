from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".lyra" / "history.db"


class ConversationMemory:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    session   TEXT    NOT NULL,
                    role      TEXT    NOT NULL,
                    content   TEXT    NOT NULL,
                    ts        REAL    NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON turns(session, id)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def add(self, session: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO turns (session, role, content, ts) VALUES (?, ?, ?, ?)",
                (session, role, content, time.time()),
            )

    def get_history(self, session: str, limit: int = 40) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM turns WHERE session = ? ORDER BY id DESC LIMIT ?",
                (session, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def list_sessions(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT session FROM turns ORDER BY MAX(ts) DESC"
            ).fetchall()
        return [r[0] for r in rows]

    def clear_session(self, session: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM turns WHERE session = ?", (session,))
