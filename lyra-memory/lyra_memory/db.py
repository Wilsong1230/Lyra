"""lyra_memory.db — schema, startup assertion, and cold-pass backups.

INVARIANT (MEMORY_SPEC): hot path appends, cold passes enrich, nothing is ever
replaced. `atoms` is the substrate and is permanent. Everything structural —
sessions, salience, entities, outcomes, dreams — is derived, nullable, and
re-derivable, so a cold pass crashing leaves the store correct.

FAILURE POLICY: loud. A schema mismatch or a missing table raises at startup
rather than degrading. Fail-open makes a broken store and an empty store
behaviourally identical, which for an emergence thesis is undetectable from
transcripts — verified empirically at 653 turns, 0 episodes, no symptom.

MIGRATION: none. The spec is explicit — the 29 essays do not decompose into
turns because the turns were never stored. Fresh DB.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import aiosqlite

from lyra_memory.config import BACKUP_DIR, BACKUP_RETENTION_DAYS, EMBED_DIM

# Bump ONLY with a deliberate schema change. Asserted at startup so a mismatch
# crashes rather than silently reading a store shaped differently than the code
# believes. Spec: "schema version assertion at startup, not a migration script
# anyone has to remember to run."
SCHEMA_VERSION = 3


class SchemaVersionError(RuntimeError):
    """Raised at startup when the store on disk is not the schema this code speaks."""


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ── HOT ─────────────────────────────────────────────────────────────────────
-- One row per turn/observation. Written on the reply path in a single
-- transaction with its vec and fts rows. Budget: <50ms. Every column below
-- `text` is COLD: nullable, written by a batch pass, and re-derivable.
CREATE TABLE IF NOT EXISTS atoms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    speaker        TEXT NOT NULL,       -- wilson | lyra | system
    source         TEXT NOT NULL,       -- cli | wakeword | ambient | vision | sandbox_read | ...
    text           TEXT NOT NULL,
    session_id     INTEGER REFERENCES sessions(id),   -- cold: segmentation
    salience       REAL,                              -- cold: salience scoring
    outcome_id     INTEGER REFERENCES outcomes(id),   -- cold: outcome consolidation
    -- Land in step 1 as nullables: cheap now, expensive later (spec "Build order").
    retrievability REAL,                              -- cold: forgetting pass (deferred)
    environment    TEXT,                              -- cli | shell | physics | bns
    run_id         INTEGER                            -- bulk containment: atom points at the run
);
CREATE INDEX IF NOT EXISTS idx_atoms_ts ON atoms(ts);
CREATE INDEX IF NOT EXISTS idx_atoms_session ON atoms(session_id);
CREATE INDEX IF NOT EXISTS idx_atoms_speaker ON atoms(speaker);
CREATE INDEX IF NOT EXISTS idx_atoms_source ON atoms(source);

-- ── COLD ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts     REAL NOT NULL,
    ended_ts       REAL NOT NULL,
    atom_count     INTEGER NOT NULL,
    -- Spec "Duration": she has timestamps and no sense of elapsed time.
    -- Cost is one subtraction. NOT injected directly — it is dream input and
    -- the material for reconstructing the shape of her history.
    gap_since_prev REAL
);

-- Open loops she owns. The line between having memory and being useful.
CREATE TABLE IF NOT EXISTS commitments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts     REAL NOT NULL,
    source_atom_id INTEGER NOT NULL REFERENCES atoms(id),
    text           TEXT NOT NULL,       -- free-form, as stated
    owner          TEXT NOT NULL,       -- lyra | wilson
    due_ts         REAL,                -- only if explicit
    status         TEXT NOT NULL,       -- open | done | dropped | superseded
    closed_ts      REAL,
    closed_atom_id INTEGER REFERENCES atoms(id)
);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);

CREATE TABLE IF NOT EXISTS dreams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    text       TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id)
);

-- Provenance. Makes dreams re-derivable and keeps the essay a LAYER over its
-- atoms rather than a competitor to them in retrieval.
CREATE TABLE IF NOT EXISTS dream_atoms (
    dream_id INTEGER NOT NULL REFERENCES dreams(id),
    atom_id  INTEGER NOT NULL REFERENCES atoms(id),
    PRIMARY KEY (dream_id, atom_id)
);

CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,           -- project | person | repo | file | topic
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS atom_entities (
    atom_id    INTEGER NOT NULL REFERENCES atoms(id),
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    confidence REAL,
    PRIMARY KEY (atom_id, entity_id)
);

CREATE TABLE IF NOT EXISTS outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_atom_id INTEGER REFERENCES atoms(id),
    predicted      TEXT NOT NULL,
    actual         TEXT NOT NULL,
    valence        REAL,
    ts             REAL NOT NULL,
    environment    TEXT
);
CREATE INDEX IF NOT EXISTS idx_outcomes_ts ON outcomes(ts);

-- One row per turn. Hot, append-only, cheap. Without it an odd response cannot
-- be reconstructed against what she was actually looking at, and MISSES —
-- queries that returned nothing above threshold — show where the store is thin.
CREATE TABLE IF NOT EXISTS context_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_id  INTEGER REFERENCES sessions(id),
    atom_ids    TEXT NOT NULL,          -- json array, what was injected
    fact_ids    TEXT NOT NULL,
    dream_ids   TEXT NOT NULL,
    budget_used INTEGER NOT NULL,       -- tokens
    misses      TEXT                    -- json: queries that returned nothing
);

-- Exact, permanent, correctable. Separate physics from atoms: facts never
-- decay, are matched by subject rather than KNN, and are corrected by
-- supersession. Routing them out of the decay pool is upstream of the decay
-- function — no exemption logic needed, they are not in the pool.
CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    subject        TEXT NOT NULL,       -- normalized key; joins entities(name) where possible
    text           TEXT NOT NULL,       -- free-form. no predicate vocabulary.
    source_atom_id INTEGER REFERENCES atoms(id),
    source_kind    TEXT NOT NULL,       -- stated | document | inferred | observed
    confidence     REAL,
    valid_from     REAL NOT NULL,
    valid_until    REAL,                -- NULL = current. stamped only by supersession.
    superseded_by  INTEGER REFERENCES facts(id),
    conflict_with  INTEGER REFERENCES facts(id)   -- suspected, unresolved
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);

-- Append-only, never updated. `traits` is a materialized view of the latest
-- state; THIS is the source of truth. The trajectory is the primary artifact
-- of the project.
CREATE TABLE IF NOT EXISTS trait_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    trait_id       INTEGER,
    trait_label    TEXT NOT NULL,
    event          TEXT NOT NULL,       -- promoted | confidence_change | tier_change | decayed | retired
    conf_before    REAL,
    conf_after     REAL,
    tier_before    TEXT,
    tier_after     TEXT,
    evidence_count INTEGER,
    dream_id       INTEGER REFERENCES dreams(id)
);
CREATE INDEX IF NOT EXISTS idx_trait_history_label ON trait_history(trait_label);

-- Bulk containment. Continuous state never becomes atoms: 60-90Hz telemetry,
-- stdout, stack traces, file contents, raw frames. The atom points DOWN at the
-- run; the run is not retrievable material.
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    environment    TEXT NOT NULL,
    started_ts     REAL NOT NULL,
    ended_ts       REAL,
    transcript_ref TEXT,
    metrics        TEXT
);

-- Every query_memory() call: timestamp, query, and the context it fired in.
-- The only way to distinguish reaching-for from firing-out-of-habit.
CREATE TABLE IF NOT EXISTS introspection_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    query   TEXT,
    context TEXT,
    n_results INTEGER
);

-- ── Identity (unchanged this pass; see spec note on traits/candidates) ──────
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

-- Ordinary key/value scratch state (affect snapshots, run markers). This is
-- NOT the facts table: `facts` is the spec's subject-keyed, permanent,
-- correctable store and must not be used as a KV bag.
CREATE TABLE IF NOT EXISTS kv_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

_CREATE_VEC_SQL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS vec_atoms USING vec0(
    embedding FLOAT[{EMBED_DIM}]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_dreams USING vec0(
    embedding FLOAT[{EMBED_DIM}]
);
CREATE VIRTUAL TABLE IF NOT EXISTS vec_candidates USING vec0(
    embedding FLOAT[{EMBED_DIM}]
);
"""

# FTS5 with external content: the index stores no copy of the text, only the
# terms. Rows are inserted explicitly on the hot path (three inserts, one
# transaction) rather than by trigger, so the write cost is visible where the
# budget is spent.
_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS atoms_fts USING fts5(
    text,
    content='atoms',
    content_rowid='id',
    tokenize='porter unicode61'
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
    # _execute() runs sync callables on aiosqlite's background thread; _conn is
    # the underlying sqlite3.Connection. aiosqlite has no public API for this.
    await conn._execute(_setup_vec, conn._conn)


async def init_db(path: Path) -> aiosqlite.Connection:
    """Open (creating if absent) the store and assert it is the expected schema.

    Raises rather than repairing. There is no migration path by design.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await load_vec_extension(conn)
    # WAL: readers (the introspection service, inspect_state) never block the
    # writer, and a crash mid-cold-pass leaves a recoverable store.
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(_CREATE_SQL)
    await conn.executescript(_CREATE_VEC_SQL)
    await conn.executescript(_CREATE_FTS_SQL)
    await conn.commit()
    await assert_schema_version(conn)
    return conn


async def assert_schema_version(conn: aiosqlite.Connection) -> None:
    """Startup assertion. A store written by a different schema crashes here.

    Spec "Failure policy": schema mismatch or missing table -> crash on start.
    """
    async with conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        await conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await conn.commit()
        return

    found = int(row[0])
    if found != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"memory store is schema v{found}, this build speaks v{SCHEMA_VERSION}. "
            "There is no migration path by design (MEMORY_SPEC 'Migration'). "
            "Move the old store aside and start fresh, or check out the matching build."
        )

    # A missing table is as fatal as a version skew, and a version row alone
    # does not prove the tables exist — CREATE IF NOT EXISTS above is silent.
    await assert_tables_present(conn)


_REQUIRED_TABLES = (
    "atoms", "vec_atoms", "atoms_fts", "sessions", "commitments", "dreams",
    "dream_atoms", "entities", "atom_entities", "outcomes", "context_log",
    "facts", "trait_history", "runs", "introspection_log", "candidates",
    "traits", "kv_state",
)


async def assert_tables_present(conn: aiosqlite.Connection) -> None:
    async with conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
    ) as cur:
        present = {r[0] for r in await cur.fetchall()}
    missing = [t for t in _REQUIRED_TABLES if t not in present]
    if missing:
        raise SchemaVersionError(
            f"memory store is missing required tables: {', '.join(missing)}"
        )


# ── Cold-pass backups ────────────────────────────────────────────────────────

def backup_before_cold_pass(db_path: Path, label: str) -> Path | None:
    """Copy the store aside before a pass that mutates it.

    Cold passes stamp columns across the store (salience, session_id,
    conflict_with) and a bad one has no undo. Retention is 30 days, not 7:
    undetected tampering contaminates every backup inside its window, so the
    window has to outlast plausible detection lag.

    Returns the backup path, or None if there is nothing to back up yet.
    """
    if not db_path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    dest = BACKUP_DIR / f"{db_path.stem}.{stamp}.{label}.db"

    # sqlite3's own backup API, NOT a file copy. In WAL mode the newest
    # committed rows live in `-wal` until a checkpoint, so copying the `.db`
    # alone silently produces a backup that is missing the most recent writes —
    # which is precisely the window a bad cold pass would need restoring from.
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()

    prune_backups()
    return dest


def prune_backups(now: float | None = None) -> list[Path]:
    """Delete backups older than the retention window. Returns what it removed."""
    if not BACKUP_DIR.exists():
        return []
    cutoff = (now if now is not None else time.time()) - BACKUP_RETENTION_DAYS * 86400
    removed: list[Path] = []
    for p in BACKUP_DIR.glob("*.db"):
        if p.stat().st_mtime < cutoff:
            p.unlink()
            removed.append(p)
    return removed
