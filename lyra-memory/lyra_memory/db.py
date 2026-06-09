from __future__ import annotations
import sqlite3
import aiosqlite
from pathlib import Path
from lyra_memory.config import EMBED_DIM

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    content           TEXT NOT NULL,
    ts                REAL NOT NULL,
    source_items_json TEXT NOT NULL,
    salience          REAL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS candidates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trait_name     TEXT NOT NULL,
    trait_value    TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    last_seen      REAL NOT NULL,
    category       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS traits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    value          TEXT NOT NULL,
    confidence     REAL NOT NULL,
    stability      TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    updated_at     REAL NOT NULL
);
"""

_CREATE_VEC_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
    embedding FLOAT[{EMBED_DIM}]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_candidates USING vec0(
    embedding FLOAT[{EMBED_DIM}]
);
"""


def _setup_vec(raw_conn: sqlite3.Connection) -> None:
    try:
        raw_conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(raw_conn)
        raw_conn.enable_load_extension(False)
    except AttributeError as exc:
        raise RuntimeError(
            "sqlite3 extension loading is unavailable on this Python build. "
            "macOS system Python disables extension loading — use Python from "
            "Homebrew (brew install python) or python.org instead."
        ) from exc


async def load_vec_extension(conn: aiosqlite.Connection) -> None:
    # _execute() runs sync callables on aiosqlite's background thread; _conn is the
    # underlying sqlite3.Connection. aiosqlite has no public API for this pattern.
    await conn._execute(_setup_vec, conn._conn)


async def init_db(path: Path) -> aiosqlite.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await load_vec_extension(conn)
    await conn.executescript(_CREATE_SQL)
    await conn.executescript(_CREATE_VEC_SQL)
    await conn.commit()
    return conn


async def get_recent_episodes(conn: aiosqlite.Connection, limit: int) -> list[dict]:
    async with conn.execute(
        "SELECT id, content, ts, source_items_json, salience "
        "FROM episodes ORDER BY ts DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"id": r[0], "content": r[1], "ts": r[2], "source_items_json": r[3], "salience": r[4]}
        for r in reversed(rows)
    ]
