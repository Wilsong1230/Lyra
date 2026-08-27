"""lyra_memory.context_log — one row per turn, hot, append-only.

Without this, an odd response cannot be reconstructed against what she was
actually looking at. MISSES are equally informative: a query that returned
nothing above threshold shows where the store is thin, and no other signal
distinguishes "retrieval found nothing" from "retrieval was never run".

Cheap enough to ship on the hot path with the rest of step 3.
"""
from __future__ import annotations

import json
import time

import aiosqlite


class ContextLog:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def write(
        self,
        *,
        atom_ids: list[int],
        fact_ids: list[int],
        dream_ids: list[int],
        budget_used: int,
        misses: list[str] | None = None,
        session_id: int | None = None,
        ts: float | None = None,
    ) -> int:
        cur = await self._conn.execute(
            "INSERT INTO context_log (ts, session_id, atom_ids, fact_ids, dream_ids,"
            " budget_used, misses) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                time.time() if ts is None else ts,
                session_id,
                json.dumps(atom_ids),
                json.dumps(fact_ids),
                json.dumps(dream_ids),
                budget_used,
                json.dumps(misses) if misses else None,
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def recent(self, limit: int = 20) -> list[dict]:
        async with self._conn.execute(
            "SELECT id, ts, session_id, atom_ids, fact_ids, dream_ids, budget_used, misses"
            " FROM context_log ORDER BY ts DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "ts": r[1], "session_id": r[2],
                "atom_ids": json.loads(r[3]), "fact_ids": json.loads(r[4]),
                "dream_ids": json.loads(r[5]), "budget_used": r[6],
                "misses": json.loads(r[7]) if r[7] else [],
            }
            for r in rows
        ]

    async def miss_rate(self, limit: int = 200) -> float:
        """Fraction of recent turns where at least one path came back empty."""
        rows = await self.recent(limit)
        if not rows:
            return 0.0
        return sum(1 for r in rows if r["misses"]) / len(rows)
