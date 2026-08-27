"""The store's schema, its version, and the vocabularies it enforces.

Invariant this schema exists to hold: **the hot path appends only.**
`ts`, `speaker`, `source`, `text` are written on the reply path and are
permanent. Everything else — `session_id`, `salience`, `retrievability`,
`outcome_id`, and every cold table below — is derived, nullable, and
re-derivable. A cold pass crashing leaves the store correct.

There is no migration path and there will not be one: the version below is
asserted at boot (see `assert_schema`), and a mismatch is a crash, not a
prompt to run something. That is why every table lands here at step 1 rather
than arriving with the pass that first writes it.
"""
from __future__ import annotations

from lyra_memory.config import EMBED_DIM

# Bumped whenever the DDL below changes in a way an existing store cannot
# satisfy. There is no migration script; a mismatch crashes on start.
SCHEMA_VERSION = 1

# ── vocabularies ─────────────────────────────────────────────────────────────
# `speaker` is enforced by a CHECK constraint: three values, stable, and both
# per-person scoping and recall's down-weighting of her own turns depend on it.
#
# `source` and `environment` are enforced in Python instead. They are expected
# to grow when embodiment lands, and a CHECK constraint on a growing
# vocabulary is exactly the migration this schema rules out. Enforcement is
# still strict and loud — a typo'd source would silently escape the
# `sandbox_read` dream exclusion, which is a safety boundary, not a nicety.

SPEAKERS: frozenset[str] = frozenset({"wilson", "lyra", "system"})
SOURCES: frozenset[str] = frozenset({"cli", "wakeword", "ambient", "vision", "sandbox_read"})
ENVIRONMENTS: frozenset[str] = frozenset({"cli", "shell", "physics", "bns"})

# A file on disk must never be able to assert what she is. Atoms from this
# source produce facts; they are never eligible dream input, and so never
# reach the candidate pool, trait promotion, or persona.
DREAM_EXCLUDED_SOURCES: frozenset[str] = frozenset({"sandbox_read"})

# `instance` is whose mind an atom belongs to; `environment` is where the body
# was. Two columns, deliberately (see DECISIONS.md). NULL means shared
# perception — an atom every instance received — and retrieval reads
# `instance IS NULL OR instance = ?`. One instance runs today.
DEFAULT_INSTANCE = "lyra"

SOURCE_KINDS: frozenset[str] = frozenset({"stated", "observed", "document", "inferred"})
COMMITMENT_STATUSES: frozenset[str] = frozenset({"open", "done", "dropped", "superseded"})
ENTITY_KINDS: frozenset[str] = frozenset({"project", "person", "repo", "file", "topic"})


HOT_SQL = f"""
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS atoms (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    speaker        TEXT NOT NULL CHECK (speaker IN ('wilson', 'lyra', 'system')),
    source         TEXT NOT NULL,
    environment    TEXT,          -- where the body was
    instance       TEXT,          -- whose mind; NULL = shared perception
    text           TEXT NOT NULL,
    run_id         INTEGER,       -- pointer into runs.db; bulk never becomes an atom
    session_id     INTEGER,       -- cold
    salience       REAL,          -- cold
    retrievability REAL,          -- cold
    outcome_id     INTEGER        -- cold
);
CREATE INDEX IF NOT EXISTS idx_atoms_ts ON atoms(ts);
CREATE INDEX IF NOT EXISTS idx_atoms_session ON atoms(session_id);
CREATE INDEX IF NOT EXISTS idx_atoms_speaker ON atoms(speaker);

CREATE TABLE IF NOT EXISTS context_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    session_id  INTEGER,
    atom_ids    TEXT NOT NULL,     -- json array of what was injected
    fact_ids    TEXT NOT NULL,
    dream_ids   TEXT NOT NULL,
    budget_used INTEGER NOT NULL,  -- tokens
    misses      TEXT               -- json: queries that returned nothing above threshold
);
CREATE INDEX IF NOT EXISTS idx_context_log_ts ON context_log(ts);
"""

# FTS5 external content: the index lives here, the text stays in `atoms`.
# Maintained by triggers rather than by the caller, so an atom cannot reach
# `atoms` without reaching the index — "no silent gaps" is the step 1
# verification target, and a trigger makes the gap structurally impossible
# rather than merely unlikely.
#
# There is no deletion path for atoms; the delete/update triggers exist so
# that if one is ever added, the index does not quietly go stale.
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS atoms_fts USING fts5(
    text,
    content='atoms',
    content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS atoms_fts_insert AFTER INSERT ON atoms BEGIN
    INSERT INTO atoms_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS atoms_fts_delete AFTER DELETE ON atoms BEGIN
    INSERT INTO atoms_fts(atoms_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS atoms_fts_update AFTER UPDATE OF text ON atoms BEGIN
    INSERT INTO atoms_fts(atoms_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO atoms_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

VEC_SQL = f"""
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

COLD_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    started_ts     REAL NOT NULL,
    ended_ts       REAL NOT NULL,
    atom_count     INTEGER NOT NULL,
    gap_since_prev REAL          -- seconds since the previous session ended
);

CREATE TABLE IF NOT EXISTS dreams (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    text       TEXT NOT NULL,
    session_id INTEGER REFERENCES sessions(id)
);
-- Provenance: what a dream was made from, so dreams stay re-derivable.
CREATE TABLE IF NOT EXISTS dream_atoms (
    dream_id INTEGER NOT NULL REFERENCES dreams(id),
    atom_id  INTEGER NOT NULL REFERENCES atoms(id),
    PRIMARY KEY (dream_id, atom_id)
);

CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL,     -- project | person | repo | file | topic
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS atom_entities (
    atom_id    INTEGER NOT NULL REFERENCES atoms(id),
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    confidence REAL,
    PRIMARY KEY (atom_id, entity_id)
);

-- The unit is the ATTEMPT, not the command. Ten failed test runs then a pass
-- is one success; abandoning is the failure.
CREATE TABLE IF NOT EXISTS outcomes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_atom_id INTEGER REFERENCES atoms(id),
    predicted      TEXT,
    actual         TEXT,
    valence        REAL,
    ts             REAL NOT NULL,
    environment    TEXT
);

-- subject + free-form text. No predicate column: most facts are not triples,
-- and a fixed predicate vocabulary is an authored schema.
CREATE TABLE IF NOT EXISTS facts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    subject        TEXT NOT NULL,
    text           TEXT NOT NULL,
    source_atom_id INTEGER REFERENCES atoms(id),
    source_kind    TEXT NOT NULL,   -- stated | observed | document | inferred
    confidence     REAL,
    valid_from     REAL NOT NULL,
    valid_until    REAL,            -- NULL = current. v1 never stamps this.
    superseded_by  INTEGER REFERENCES facts(id),   -- unused in v1
    conflict_with  INTEGER REFERENCES facts(id)    -- flagged, unresolved
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject);

CREATE TABLE IF NOT EXISTS commitments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts     REAL NOT NULL,
    source_atom_id INTEGER REFERENCES atoms(id),
    text           TEXT NOT NULL,
    owner          TEXT NOT NULL,   -- lyra | wilson
    due_ts         REAL,
    status         TEXT NOT NULL,   -- open | done | dropped | superseded
    closed_ts      REAL,
    closed_atom_id INTEGER REFERENCES atoms(id)
);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);

CREATE TABLE IF NOT EXISTS traits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    value          TEXT NOT NULL,
    confidence     REAL NOT NULL,
    stability      TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trait_name     TEXT NOT NULL,
    trait_value    TEXT NOT NULL,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    last_seen      REAL NOT NULL,
    category       TEXT NOT NULL,
    evidence_text  TEXT
);

-- Append-only. `traits` is a materialized view of the latest state; this is
-- the source of truth, and the trajectory is the primary artifact.
CREATE TABLE IF NOT EXISTS trait_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    trait_id       INTEGER NOT NULL REFERENCES traits(id),
    trait_label    TEXT NOT NULL,
    event          TEXT NOT NULL,
    conf_before    REAL,
    conf_after     REAL,
    tier_before    TEXT,
    tier_after     TEXT,
    evidence_count INTEGER NOT NULL,
    dream_id       INTEGER REFERENCES dreams(id)
);
CREATE INDEX IF NOT EXISTS idx_trait_history_trait ON trait_history(trait_id);
"""

# Separate file. Bulk, prunable, and never joined to in a hot query.
# `atoms.run_id` is a plain integer: SQLite cannot enforce a foreign key
# across files, and pruning runs must leave the atoms intact anyway.
RUNS_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    environment    TEXT,
    started_ts     REAL,
    ended_ts       REAL,
    transcript_ref TEXT,
    metrics        TEXT
);
"""


def validate_atom(speaker: str, source: str, environment: str | None) -> None:
    """Fail loud on an out-of-vocabulary atom, before anything is written."""
    if speaker not in SPEAKERS:
        raise ValueError(
            f"unknown speaker {speaker!r}; expected one of {sorted(SPEAKERS)}"
        )
    if source not in SOURCES:
        raise ValueError(
            f"unknown source {source!r}; expected one of {sorted(SOURCES)}. "
            "Add it to SOURCES deliberately — source gates the sandbox_read "
            "dream exclusion."
        )
    if environment is not None and environment not in ENVIRONMENTS:
        raise ValueError(
            f"unknown environment {environment!r}; expected one of {sorted(ENVIRONMENTS)}"
        )
