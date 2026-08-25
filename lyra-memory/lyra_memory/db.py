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
-- Atoms are the RETRIEVAL unit: one row per turn/observation/reflection, with
-- salience computed at write time. `episodes` is the consolidation layer above
-- them (one dream essay, referencing its constituent atoms via atoms.episode_id)
-- and deliberately does NOT compete in KNN retrieval — a 3,000-character essay
-- outranks and drowns out everything it was summarised from.
CREATE TABLE IF NOT EXISTS atoms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    ts         REAL NOT NULL,
    type       TEXT NOT NULL,
    role       TEXT,
    salience   REAL NOT NULL,
    episode_id INTEGER REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_atoms_ts ON atoms(ts);
CREATE INDEX IF NOT EXISTS idx_atoms_episode ON atoms(episode_id);
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
CREATE VIRTUAL TABLE IF NOT EXISTS vec_atoms USING vec0(
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
    await _migrate(conn)
    return conn


async def _migrate(conn: aiosqlite.Connection) -> None:
    async with conn.execute("PRAGMA table_info(episodes)") as cur:
        columns = {row[1] for row in await cur.fetchall()}
    if "salience" not in columns:
        await conn.execute("ALTER TABLE episodes ADD COLUMN salience REAL DEFAULT 0.0")
        await conn.commit()

    await _migrate_candidate_vectors(conn)
    await _migrate_episodes_to_atoms(conn)


_CANDIDATE_VEC_MARKER = "_migration_candidate_vectors_v2"


async def _migrate_candidate_vectors(conn: aiosqlite.Connection) -> None:
    """Re-embed candidate vectors from the DESCRIPTION instead of the label.

    Existing vec_candidates rows were built from trait_name. Dedup now matches
    on trait_value, so the stored vectors are in the wrong space and would
    never match correctly. Rebuild them once, and re-run dedup at the new
    threshold so candidates that should have merged all along do.

    Runs at most once, guarded by a marker row in `facts`.
    """
    async with conn.execute(
        "SELECT 1 FROM facts WHERE key = ?", (_CANDIDATE_VEC_MARKER,)
    ) as cur:
        if await cur.fetchone() is not None:
            return

    async with conn.execute(
        "SELECT id, trait_name, trait_value, evidence_count, last_seen, category "
        "FROM candidates ORDER BY id"
    ) as cur:
        rows = await cur.fetchall()

    if rows:
        import math
        from lyra_memory.config import CANDIDATE_DEDUP_THRESHOLD
        from lyra_memory.embeddings import embed

        threshold = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)

        # Incremental re-clustering in original insertion order, mirroring
        # CandidatePool.add_observation so the migrated pool is exactly what
        # the new code would have produced.
        kept: list[dict] = []
        vectors: list[tuple[float, ...]] = []
        import struct

        for _id, name, value, count, last_seen, category in rows:
            raw = await embed(value)
            v = struct.unpack(f"{len(raw)//4}f", raw)

            best_i, best_d = None, None
            for i, ov in enumerate(vectors):
                d = math.sqrt(sum((a - b) ** 2 for a, b in zip(v, ov)))
                if best_d is None or d < best_d:
                    best_i, best_d = i, d

            if best_d is not None and best_d < threshold:
                kept[best_i]["evidence_count"] += count
                kept[best_i]["last_seen"] = max(kept[best_i]["last_seen"], last_seen)
            else:
                kept.append({
                    "trait_name": name, "trait_value": value,
                    "evidence_count": count, "last_seen": last_seen,
                    "category": category, "raw": raw,
                })
                vectors.append(v)

        await conn.execute("DELETE FROM candidates")
        await conn.execute("DELETE FROM vec_candidates")
        for c in kept:
            cur2 = await conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category)"
                " VALUES (?, ?, ?, ?, ?)",
                (c["trait_name"], c["trait_value"], c["evidence_count"],
                 c["last_seen"], c["category"]),
            )
            await conn.execute(
                "INSERT INTO vec_candidates(rowid, embedding) VALUES (?, ?)",
                (cur2.lastrowid, c["raw"]),
            )
        print(
            f"[migration] candidate vectors rebuilt from descriptions: "
            f"{len(rows)} -> {len(kept)} candidates"
        )

    # Value must be valid JSON — StructuredState.get_all_facts() json.loads
    # every row in this table.
    import json
    import time as _time

    await conn.execute(
        "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
        (_CANDIDATE_VEC_MARKER, json.dumps(True), _time.time()),
    )
    await conn.commit()


_ATOMS_MARKER = "_migration_episodes_to_atoms_v1"


async def _migrate_episodes_to_atoms(conn: aiosqlite.Connection) -> None:
    """Decompose existing episodes into atoms via their source_items_json.

    Each episode stored the working-memory items it was built from, including
    the per-item score — which is exactly the salience an atom needs, so the
    decomposition is lossless rather than reconstructed. Episodes whose
    snapshot is empty or unparseable keep existing in the consolidation layer
    with no atoms beneath them; nothing is deleted.
    """
    async with conn.execute("SELECT 1 FROM facts WHERE key = ?", (_ATOMS_MARKER,)) as cur:
        if await cur.fetchone() is not None:
            return

    import json
    import time as _time

    from lyra_memory.embeddings import embed

    async with conn.execute(
        "SELECT id, ts, source_items_json FROM episodes ORDER BY id"
    ) as cur:
        episodes = await cur.fetchall()

    written = 0
    undecomposable = 0
    for episode_id, ep_ts, snapshot in episodes:
        try:
            items = json.loads(snapshot) if snapshot else []
        except (json.JSONDecodeError, TypeError):
            items = []
        if not items:
            undecomposable += 1
            continue
        for it in items:
            content = it.get("content")
            if not content:
                continue
            cur2 = await conn.execute(
                "INSERT INTO atoms (content, ts, type, role, salience, episode_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (content, it.get("ts") or ep_ts, it.get("type") or "conversation",
                 it.get("role"), float(it.get("score") or 0.0), episode_id),
            )
            await conn.execute(
                "INSERT INTO vec_atoms(rowid, embedding) VALUES (?, ?)",
                (cur2.lastrowid, await embed(content)),
            )
            written += 1

    if episodes:
        print(
            f"[migration] episodes -> atoms: {len(episodes)} episodes decomposed into "
            f"{written} atoms ({undecomposable} retained in the consolidation layer only)"
        )

    await conn.execute(
        "INSERT OR REPLACE INTO facts (key, value, updated_at) VALUES (?, ?, ?)",
        (_ATOMS_MARKER, json.dumps(True), _time.time()),
    )
    await conn.commit()


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
