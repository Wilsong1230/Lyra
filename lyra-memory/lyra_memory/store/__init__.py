"""The store: the hot append path and the connection everything else uses.

The hot path is the only thing that runs per turn, and it does one job —
append. Per turn: the user atom and her atom (each an insert into `atoms`,
one into `vec_atoms`, one into `atoms_fts` via trigger) plus one
`context_log` row, in a single transaction, under 50ms.

No enrichment, no dream, no consolidation. Everything structural is derived
later by a cold pass, from atoms that are already permanent.

**No try/except around ingest.** Fail-open makes a broken store and an empty
store behaviourally identical, which for an emergence thesis is undetectable
from transcripts — 653 turns produced 0 episodes with no symptom. If a write
fails, the turn fails.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import aiosqlite

from lyra_memory.embeddings import embed
from lyra_memory.store.schema import (
    COLD_SQL,
    DEFAULT_INSTANCE,
    FTS_SQL,
    HOT_SQL,
    RUNS_SQL,
    SCHEMA_VERSION,
    VEC_SQL,
    validate_atom,
)


def _load_vec_sync(raw_conn: sqlite3.Connection) -> None:
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
    await conn._execute(_load_vec_sync, conn._conn)


class Store:
    """Owns both files: the substrate (`store.db`) and the bulk (`runs.db`)."""

    def __init__(self, path: Path, runs_path: Path) -> None:
        self.path = path
        self.runs_path = runs_path
        self.db: aiosqlite.Connection | None = None
        self.runs: aiosqlite.Connection | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @classmethod
    async def open(cls, path: Path | None = None, runs_path: Path | None = None) -> "Store":
        from lyra_memory.config import RUNS_PATH, STORE_PATH

        path = Path(path) if path is not None else STORE_PATH
        if runs_path is None:
            runs_path = path.parent / "runs.db" if path != STORE_PATH else RUNS_PATH

        store = cls(path, Path(runs_path))
        await store._connect()
        return store

    async def _connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runs_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = await aiosqlite.connect(self.path)
        await load_vec_extension(self.db)
        # WAL: a reader (the introspection API) must never block the writer,
        # and a crash mid-cold-pass must not corrupt the substrate.
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA foreign_keys=ON")
        await self.db.executescript(HOT_SQL)
        await self.db.executescript(VEC_SQL)
        await self.db.executescript(COLD_SQL)
        await self.db.executescript(FTS_SQL)
        await self.db.execute(
            "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await self.db.commit()

        self.runs = await aiosqlite.connect(self.runs_path)
        await self.runs.execute("PRAGMA journal_mode=WAL")
        await self.runs.executescript(RUNS_SQL)
        await self.runs.commit()

        from lyra_memory.store.integrity import assert_schema

        await assert_schema(self.db)

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()
            self.db = None
        if self.runs is not None:
            await self.runs.close()
            self.runs = None

    # ── the hot path ─────────────────────────────────────────────────────────

    async def append_atom(
        self,
        speaker: str,
        source: str,
        text: str,
        environment: str | None = None,
        instance: str | None = DEFAULT_INSTANCE,
        run_id: int | None = None,
        ts: float | None = None,
        vec: bytes | None = None,
        commit: bool = True,
    ) -> int:
        """Append one atom, its vector, and its FTS entry.

        The FTS row is written by a trigger, so an atom cannot land in `atoms`
        without landing in the index. Validation runs before any write, so a
        rejected atom leaves nothing behind.

        `vec` lets the caller hand in an embedding it already has. The hot
        path embeds the incoming text at step 1 to retrieve with, then appends
        that same text at step 4 — embedding it twice would spend ~10ms of a
        50ms budget computing a value already in hand.
        """
        validate_atom(speaker, source, environment)
        if not text:
            raise ValueError("an atom must have text; bulk belongs in runs")

        if vec is None:
            vec = await embed(text)
        cur = await self.db.execute(
            "INSERT INTO atoms (ts, speaker, source, environment, instance, text, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts if ts is not None else time.time(), speaker, source,
             environment, instance, text, run_id),
        )
        atom_id = cur.lastrowid
        await self.db.execute(
            "INSERT INTO vec_atoms(rowid, embedding) VALUES (?, ?)", (atom_id, vec)
        )
        if commit:
            await self.db.commit()
        return atom_id

    async def ingest_turn(
        self,
        user_text: str,
        lyra_text: str,
        source: str = "cli",
        environment: str | None = None,
        instance: str | None = DEFAULT_INSTANCE,
        speaker: str = "wilson",
        injected: dict | None = None,
        ts: float | None = None,
        user_vec: bytes | None = None,
    ) -> tuple[int, int]:
        """One turn: her interlocutor's atom, her atom, and the context_log row.

        All of it in one transaction — a turn that half-lands is worse than a
        turn that fails, because only the second is visible.

        `user_vec` is the embedding retrieval already computed for this text.
        """
        now = ts if ts is not None else time.time()
        user_atom = await self.append_atom(
            speaker=speaker, source=source, text=user_text,
            environment=environment, instance=instance, ts=now,
            vec=user_vec, commit=False,
        )
        lyra_atom = await self.append_atom(
            speaker="lyra", source=source, text=lyra_text,
            environment=environment, instance=instance, ts=now, commit=False,
        )
        await self._log_context(injected or {}, ts=now, commit=False)
        await self.db.commit()
        return user_atom, lyra_atom

    async def _log_context(
        self, injected: dict, ts: float | None = None, commit: bool = True
    ) -> int:
        """One row per turn recording what was injected — and what missed.

        Misses are as informative as hits: they show where the store is thin
        and which queries have no home. Written from day one.
        """
        cur = await self.db.execute(
            "INSERT INTO context_log (ts, session_id, atom_ids, fact_ids, dream_ids,"
            " budget_used, misses) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ts if ts is not None else time.time(),
                injected.get("session_id"),
                json.dumps(injected.get("atom_ids", [])),
                json.dumps(injected.get("fact_ids", [])),
                json.dumps(injected.get("dream_ids", [])),
                int(injected.get("budget_used", 0)),
                json.dumps(injected["misses"]) if injected.get("misses") else None,
            ),
        )
        if commit:
            await self.db.commit()
        return cur.lastrowid

    # ── bulk ─────────────────────────────────────────────────────────────────

    async def append_run(
        self,
        environment: str | None = None,
        transcript: str | None = None,
        metrics: dict | None = None,
        started_ts: float | None = None,
        ended_ts: float | None = None,
    ) -> int:
        """Store bulk — stdout, telemetry, frames, file contents — off to the side.

        Hard rule: bulk never becomes atoms. The atom records the event and
        points here; a 3,000-word document must not become a retrieval unit.
        """
        now = time.time()
        cur = await self.runs.execute(
            "INSERT INTO runs (environment, started_ts, ended_ts, transcript_ref, metrics)"
            " VALUES (?, ?, ?, ?, ?)",
            (environment, started_ts if started_ts is not None else now,
             ended_ts if ended_ts is not None else now, transcript,
             json.dumps(metrics) if metrics is not None else None),
        )
        await self.runs.commit()
        return cur.lastrowid

    # ── integrity ────────────────────────────────────────────────────────────

    async def find_index_gaps(self) -> dict[str, list[int]]:
        """Atoms missing from either index.

        An atom in `atoms` but not `vec_atoms` is invisible to semantic
        recall; one missing from `atoms_fts` is invisible to lexical. Neither
        raises anything on its own, which is exactly why this exists.
        """
        async with self.db.execute(
            "SELECT a.id FROM atoms a"
            " LEFT JOIN vec_atoms v ON v.rowid = a.id WHERE v.rowid IS NULL"
        ) as cur:
            missing_vec = [r[0] for r in await cur.fetchall()]
        async with self.db.execute(
            "SELECT a.id FROM atoms a"
            " LEFT JOIN atoms_fts f ON f.rowid = a.id WHERE f.rowid IS NULL"
        ) as cur:
            missing_fts = [r[0] for r in await cur.fetchall()]
        return {"missing_vec": missing_vec, "missing_fts": missing_fts}
