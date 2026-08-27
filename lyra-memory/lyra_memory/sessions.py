"""lyra_memory.sessions — segmentation, and the only thing that knows about time.

A pure component: it reads `atoms.ts` and writes `sessions` plus
`atoms.session_id`. Nothing it writes is load-bearing — re-running it over a
frozen `atoms` table reproduces the same rows, which is what makes it safe to
change the boundary rule later.

DURATION. She has timestamps and no sense of elapsed time: nothing represented
"we haven't talked in three days." RelationalDrive feels the absence via its
recurrence clock, but memory never recorded that it happened. `gap_since_prev`
is written here, in the same pass that creates the session row. Cost is one
subtraction.

What it enables: gap becomes dream input (a long absence is a legitimate thing
to reflect on); session recall can carry it ("that was after a long break");
and it distinguishes a continuous month from a month with one conversation in
it, which atom count alone cannot.

NOT INJECTED DIRECTLY. Time since last session belongs in the prompt as ambient
context, not as retrieved memory. The stored gap is for reflection and for
reconstructing the shape of her history.
"""
from __future__ import annotations

import aiosqlite

from lyra_memory.config import SESSION_GAP_SECONDS


class Segmenter:
    def __init__(self, conn: aiosqlite.Connection, gap_seconds: float = SESSION_GAP_SECONDS) -> None:
        self._conn = conn
        self._gap = gap_seconds

    async def run(self) -> list[int]:
        """Segment every unassigned atom. Returns the session ids written.

        Idempotent over already-assigned atoms: it only ever considers atoms
        whose session_id is NULL, so a crash mid-pass costs the tail, not the
        store.
        """
        async with self._conn.execute(
            "SELECT id, ts FROM atoms WHERE session_id IS NULL ORDER BY ts ASC, id ASC"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return []

        groups: list[list[tuple[int, float]]] = [[rows[0]]]
        for atom_id, ts in rows[1:]:
            if ts - groups[-1][-1][1] > self._gap:
                groups.append([(atom_id, ts)])
            else:
                groups[-1].append((atom_id, ts))

        async with self._conn.execute(
            "SELECT ended_ts FROM sessions ORDER BY ended_ts DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        prev_end: float | None = row[0] if row else None

        session_ids: list[int] = []
        for group in groups:
            started, ended = group[0][1], group[-1][1]
            gap = (started - prev_end) if prev_end is not None else None
            cur2 = await self._conn.execute(
                "INSERT INTO sessions (started_ts, ended_ts, atom_count, gap_since_prev)"
                " VALUES (?, ?, ?, ?)",
                (started, ended, len(group), gap),
            )
            session_id = cur2.lastrowid
            await self._conn.executemany(
                "UPDATE atoms SET session_id = ? WHERE id = ?",
                [(session_id, a) for a, _ in group],
            )
            session_ids.append(session_id)
            prev_end = ended

        await self._conn.commit()
        return session_ids

    async def latest(self) -> dict | None:
        async with self._conn.execute(
            "SELECT id, started_ts, ended_ts, atom_count, gap_since_prev"
            " FROM sessions ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def get(self, session_id: int) -> dict | None:
        async with self._conn.execute(
            "SELECT id, started_ts, ended_ts, atom_count, gap_since_prev"
            " FROM sessions WHERE id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()
        return _row(row) if row else None

    async def atoms_for(self, session_id: int) -> list[dict]:
        async with self._conn.execute(
            "SELECT id, ts, speaker, source, text FROM atoms WHERE session_id = ?"
            " ORDER BY ts ASC, id ASC",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "ts": r[1], "speaker": r[2], "source": r[3], "text": r[4]}
            for r in rows
        ]


def _row(r) -> dict:
    return {
        "id": r[0], "started_ts": r[1], "ended_ts": r[2],
        "atom_count": r[3], "gap_since_prev": r[4],
    }
