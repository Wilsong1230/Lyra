"""lyra_memory.commitments — open loops she owns.

The line between having memory and being useful. Without this table, "I'll look
at that tomorrow" is an atom, retrievable by luck.

EXTRACTION is cold, during dream — the same pass as facts, different output.
Dream reads a session and asks: did anyone state an intention to do something
later? `owner` distinguishes hers from Wilson's, and both matter — she should
be able to say "you said you'd migrate the DB."

CLOSURE is also cold. Dream checks open commitments against the session's atoms
and outcomes. NEVER auto-closed on time alone: a due date passing means
overdue, not done. `dropped` requires evidence of abandonment — an explicit
statement, or supersession by a conflicting commitment. Silent aging into
`dropped` would let her quietly forget things she said she'd do, which is the
exact failure this table exists to prevent.

RETRIEVAL is not KNN. This is the one store that surfaces UNPROMPTED:
everything else is retrieved on relevance; open loops assert themselves.
"""
from __future__ import annotations

import time

import aiosqlite

from lyra_memory.config import COMMITMENT_INJECT_LIMIT

_COLS = (
    "id, created_ts, source_atom_id, text, owner, due_ts, status, closed_ts, closed_atom_id"
)

OWNERS = frozenset({"lyra", "wilson"})
STATUSES = frozenset({"open", "done", "dropped", "superseded"})


class CommitmentStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        text: str,
        owner: str,
        source_atom_id: int,
        *,
        due_ts: float | None = None,
        created_ts: float | None = None,
    ) -> int:
        if owner not in OWNERS:
            raise ValueError(f"commitment owner must be one of {sorted(OWNERS)}, got {owner!r}")
        created_ts = time.time() if created_ts is None else created_ts
        cur = await self._conn.execute(
            "INSERT INTO commitments (created_ts, source_atom_id, text, owner, due_ts, status)"
            " VALUES (?, ?, ?, ?, ?, 'open')",
            (created_ts, source_atom_id, text, owner, due_ts),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def open_commitments(self) -> list[dict]:
        """Direct query, ordered by due then age. Not KNN."""
        async with self._conn.execute(
            f"SELECT {_COLS} FROM commitments WHERE status = 'open'"
            " ORDER BY due_ts IS NULL, due_ts ASC, created_ts ASC"
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def for_injection(self, limit: int = COMMITMENT_INJECT_LIMIT) -> list[dict]:
        """Injected when the count is nonzero. Small budget — a few lines.

        Truncate to the OLDEST and the SOONEST-DUE if there are many: those are
        the two that a backlog actually owes an answer for, and taking the head
        of one ordering alone would hide the other.
        """
        rows = await self.open_commitments()
        if len(rows) <= limit:
            return rows

        soonest = [r for r in rows if r["due_ts"] is not None][: max(1, limit // 2)]
        chosen = {r["id"]: r for r in soonest}
        for r in sorted(rows, key=lambda r: r["created_ts"]):
            if len(chosen) >= limit:
                break
            chosen.setdefault(r["id"], r)
        return sorted(
            chosen.values(),
            key=lambda r: (r["due_ts"] is None, r["due_ts"] or 0, r["created_ts"]),
        )

    async def close(
        self, commitment_id: int, closed_atom_id: int, *, closed_ts: float | None = None
    ) -> None:
        """Match found -> status=done, stamp closed_atom_id.

        Requires the atom that closed it. A commitment marked done with no
        evidence of what closed it is indistinguishable from one that was
        quietly dropped.
        """
        await self._conn.execute(
            "UPDATE commitments SET status = 'done', closed_ts = ?, closed_atom_id = ?"
            " WHERE id = ? AND status = 'open'",
            (time.time() if closed_ts is None else closed_ts, closed_atom_id, commitment_id),
        )
        await self._conn.commit()

    async def drop(self, commitment_id: int, evidence_atom_id: int) -> None:
        """`dropped` REQUIRES evidence of abandonment — an explicit statement,
        or supersession by a conflicting commitment. There is deliberately no
        time-based path into this state."""
        await self._conn.execute(
            "UPDATE commitments SET status = 'dropped', closed_ts = ?, closed_atom_id = ?"
            " WHERE id = ? AND status = 'open'",
            (time.time(), evidence_atom_id, commitment_id),
        )
        await self._conn.commit()

    async def supersede(self, commitment_id: int, evidence_atom_id: int) -> None:
        await self._conn.execute(
            "UPDATE commitments SET status = 'superseded', closed_ts = ?, closed_atom_id = ?"
            " WHERE id = ? AND status = 'open'",
            (time.time(), evidence_atom_id, commitment_id),
        )
        await self._conn.commit()

    async def overdue(self, now: float | None = None) -> list[dict]:
        """Open, past due. OVERDUE, NOT DONE — and deliberately not a valence
        source: making her feel bad about a backlog is a design choice, not an
        emergent one, and it is the wrong one."""
        now = time.time() if now is None else now
        return [
            r for r in await self.open_commitments()
            if r["due_ts"] is not None and r["due_ts"] < now
        ]

    async def open_count(self) -> int:
        """A legitimate BoredomDrive input: an unclosed loop is a reason to act."""
        async with self._conn.execute(
            "SELECT COUNT(*) FROM commitments WHERE status = 'open'"
        ) as cur:
            return (await cur.fetchone())[0]


def _row(r) -> dict:
    return {
        "id": r[0], "created_ts": r[1], "source_atom_id": r[2], "text": r[3],
        "owner": r[4], "due_ts": r[5], "status": r[6], "closed_ts": r[7],
        "closed_atom_id": r[8],
    }
