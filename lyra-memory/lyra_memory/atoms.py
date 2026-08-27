"""lyra_memory.atoms — the substrate.

Turns are permanent. An atom is one turn, observation, or event: `ts`,
`speaker`, `source`, `text`, and nothing else that the reply path has to think
about. Every other column is cold — nullable, written by a batch pass, and
re-derivable — so a cold pass crashing leaves the store correct.

WHY NOT ESSAYS
──────────────
Episodes used to be one dream-cycle essay: ~3,000 characters, composite over
~10 turns. A live assembled system prompt reached 44,486 characters, ~43,000 of
it retrieved essay; retrieved text carried its own `##` headings and was
structurally indistinguishable from the prompt's own scaffolding; and salience
was max(score) over the batch, which saturates — 13 of 29 episodes sat at
0.933. Everything downstream (segmentation, clustering, two-pool retrieval)
assumes the unit is small, numerous and comparable. Clustering essays yields
clusters of essays.

PERCEPTION POLICY
─────────────────
Ambient and wakeword are telemetry, not atoms — a sound classification is a
measurement; an image she looked at is an event. Telemetry reaches the
substrate only on a CATEGORY CHANGE, not once per classification. Vision atoms
are her DESCRIPTION of a frame, never the frame: irreversible compression, so
the frame goes to `runs` and the atom points at it.
"""
from __future__ import annotations

import time

import aiosqlite

from lyra_memory.config import SPEAKERS, TELEMETRY_SOURCES
from lyra_memory.embeddings import embed


class AtomStore:
    """Append-only writer over `atoms` + `vec_atoms` + `atoms_fts`."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def append(
        self,
        speaker: str,
        source: str,
        text: str,
        *,
        ts: float | None = None,
        environment: str | None = None,
        run_id: int | None = None,
    ) -> int:
        """Write one atom. Three inserts, one transaction. Budget: <50ms.

        No enrichment, no dream, no consolidation — those are cold passes.
        Raises on failure: a memory that silently drops turns is
        indistinguishable from an empty one (spec "Failure policy").
        """
        if speaker not in SPEAKERS:
            raise ValueError(f"unknown speaker {speaker!r}; expected one of {sorted(SPEAKERS)}")
        if not text:
            raise ValueError("refusing to write an empty atom")

        ts = time.time() if ts is None else ts
        vec = await embed(text)

        cur = await self._conn.execute(
            "INSERT INTO atoms (ts, speaker, source, text, environment, run_id)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, speaker, source, text, environment, run_id),
        )
        atom_id = cur.lastrowid
        await self._conn.execute(
            "INSERT INTO vec_atoms(rowid, embedding) VALUES (?, ?)", (atom_id, vec)
        )
        await self._conn.execute(
            "INSERT INTO atoms_fts(rowid, text) VALUES (?, ?)", (atom_id, text)
        )
        await self._conn.commit()
        return atom_id

    async def get(self, atom_id: int) -> dict | None:
        async with self._conn.execute(
            "SELECT id, ts, speaker, source, text, session_id, salience, outcome_id,"
            " retrievability, environment, run_id FROM atoms WHERE id = ?",
            (atom_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row_to_atom(row) if row else None

    async def recent(self, limit: int, *, before_ts: float | None = None) -> list[dict]:
        """Plain SQL recency — the temporal path. Does not compete in KNN."""
        if before_ts is None:
            sql, args = (
                "SELECT id, ts, speaker, source, text, session_id, salience, outcome_id,"
                " retrievability, environment, run_id FROM atoms"
                " ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        else:
            sql, args = (
                "SELECT id, ts, speaker, source, text, session_id, salience, outcome_id,"
                " retrievability, environment, run_id FROM atoms WHERE ts < ?"
                " ORDER BY ts DESC LIMIT ?",
                (before_ts, limit),
            )
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [_row_to_atom(r) for r in rows]

    async def count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM atoms") as cur:
            return (await cur.fetchone())[0]


def is_atom_worthy(source: str, *, category_change: bool = False) -> bool:
    """Perception containment: does this observation belong in the substrate?

    Ambient and wakeword are telemetry. First appearance of a new sound class,
    or something salient, is an event and gets a row; a row per classification
    is a measurement stream and would swamp everything else.

    Moot while ambient and webcam are disabled. The constraint exists so they
    are not re-enabled pointed straight at the substrate.
    """
    if source in TELEMETRY_SOURCES:
        return category_change
    return True


def _row_to_atom(row) -> dict:
    return {
        "id": row[0], "ts": row[1], "speaker": row[2], "source": row[3],
        "text": row[4], "session_id": row[5], "salience": row[6],
        "outcome_id": row[7], "retrievability": row[8], "environment": row[9],
        "run_id": row[10],
    }
