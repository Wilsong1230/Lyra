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

    async def add_observation(
        self,
        trait_name: str,
        trait_value: str,
        category: str,
        closed_vocabulary: bool = False,
    ) -> None:
        """Add one unit of evidence for a trait.

        closed_vocabulary=True selects EXACT-NAME dedup and keeps the row out
        of the semantic index entirely. Use it for callers whose trait names
        come from a fixed set (see OutcomeConsolidator): those names are
        generated from one template and are lexically near-identical, so
        semantic matching merges outright opposites — 'persists under
        frustration' and 'abandons under frustration' sit at L2 0.746, inside
        any threshold loose enough to merge genuine duplicates. Excluding them
        from the vec index also stops the first insert of one developmental
        name from matching another before either has a row to match by name.

        The default (False) is the open vocabulary: dream-generated names are
        arbitrary, so matching is semantic on the DESCRIPTION, not the label.
        """
        if closed_vocabulary:
            await self._add_exact(trait_name, trait_value, category)
            return

        ts = time.time()
        # Embed the description, not the label.
        vec_bytes = await embed(trait_value)

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

    async def _add_exact(self, trait_name: str, trait_value: str, category: str) -> None:
        """Exact-name dedup, no embedding and no vec row."""
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, evidence_count FROM candidates WHERE trait_name = ?", (trait_name,)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            await self._conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category)"
                " VALUES (?, ?, 1, ?, ?)",
                (trait_name, trait_value, ts, category),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT(exact) {trait_name!r}")
        else:
            new_count = row[1] + 1
            await self._conn.execute(
                "UPDATE candidates SET trait_value = ?, evidence_count = ?, last_seen = ? WHERE id = ?",
                (trait_value, new_count, ts, row[0]),
            )
            print(
                f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT(exact) "
                f"{trait_name!r} → count={new_count}"
            )
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
