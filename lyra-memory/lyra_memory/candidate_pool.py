from __future__ import annotations
import math
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import CANDIDATE_DEDUP_THRESHOLD
from lyra_memory.embeddings import embed
from lyra_memory.models import Candidate

# cosine distance threshold converted to L2 threshold for normalized vectors:
# cosine_dist = L2_dist² / 2  ⟹  L2_threshold = sqrt(2 * cosine_threshold)
_L2_THRESHOLD = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)


class CandidatePool:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add_observation(self, trait_name: str, trait_value: str, category: str) -> None:
        ts = time.time()
        vec_bytes = await embed(trait_name)

        nearest = None
        try:
            async with self._conn.execute(
                "SELECT rowid, distance FROM vec_candidates "
                "WHERE embedding MATCH ? AND k = 1 "
                "ORDER BY distance",
                (vec_bytes,),
            ) as cur:
                nearest = await cur.fetchone()
        except Exception as exc:
            import sqlite3
            # sqlite-vec raises OperationalError when vec_candidates is empty (no index segment)
            underlying = exc.__cause__ if exc.__cause__ is not None else exc
            if not isinstance(underlying, (sqlite3.OperationalError, sqlite3.DatabaseError)):
                raise
            nearest = None

        if nearest and nearest[1] < _L2_THRESHOLD:
            candidate_id = nearest[0]
            async with self._conn.execute(
                "SELECT evidence_count FROM candidates WHERE id = ?", (candidate_id,)
            ) as cur:
                row = await cur.fetchone()
            new_count = row[0] + 1
            await self._conn.execute(
                "UPDATE candidates SET trait_value = ?, evidence_count = ?, last_seen = ? WHERE id = ?",
                (trait_value, new_count, ts, candidate_id),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT id={candidate_id} → count={new_count}")
        else:
            cur = await self._conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category)"
                " VALUES (?, ?, 1, ?, ?)",
                (trait_name, trait_value, ts, category),
            )
            candidate_rowid = cur.lastrowid
            await self._conn.execute(
                "INSERT INTO vec_candidates(rowid, embedding) VALUES (?, ?)",
                (candidate_rowid, vec_bytes),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT {trait_name!r} category={category!r}")

        await self._conn.commit()

    async def get_candidates(self, min_evidence: int = 1) -> list[Candidate]:
        async with self._conn.execute(
            "SELECT id, trait_name, trait_value, evidence_count, last_seen, category"
            " FROM candidates WHERE evidence_count >= ? ORDER BY evidence_count DESC",
            (min_evidence,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Candidate(id=r[0], trait_name=r[1], trait_value=r[2], evidence_count=r[3], last_seen=r[4], category=r[5])
            for r in rows
        ]
