from __future__ import annotations
import math
import struct
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import CANDIDATE_DEDUP_THRESHOLD, EMBED_DIM
from lyra_memory.embeddings import embed
from lyra_memory.models import Candidate

# cosine distance threshold converted to L2 threshold for normalized vectors:
# cosine_dist = L2_dist² / 2  ⟹  L2_threshold = sqrt(2 * cosine_threshold)
_L2_THRESHOLD = math.sqrt(2 * CANDIDATE_DEDUP_THRESHOLD)


def _embedding_text(trait_value: str, evidence: str | None = None) -> str:
    """What actually gets embedded for dedup: description + evidence.

    Never the label. Labels are short, model-invented, and carry almost no
    signal — 'analytical_orientation' and 'analytical_approach' name two
    different behaviours while their descriptions sit 1.22 apart. Matching on
    them is why 70 candidates produced 60 singletons and one promoted trait.

    Evidence is the concrete observation behind the claim. The description
    alone is often generic enough that distinct behaviours read alike;
    grounding it in what actually happened separates them. When no evidence is
    supplied this returns the description unchanged — the previous behaviour,
    with nothing invented to fill the gap.
    """
    if evidence:
        return f"{trait_value}\n{evidence}"
    return trait_value


def _unpack(raw: bytes) -> list[float]:
    return list(struct.unpack(f"{EMBED_DIM}f", raw))


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{EMBED_DIM}f", *vec)


def _merge_centroid(stored: bytes, incoming: bytes, stored_weight: int) -> bytes:
    """Running mean of a cluster's member vectors, renormalized.

    A cluster must be represented by its members, not by whichever one
    happened to arrive first: with a first-member representative, the same
    observations in a different order produce a different pool. The stored
    vector is the mean of the `stored_weight` vectors already merged plus the
    new one.
    """
    a, b = _unpack(stored), _unpack(incoming)
    n = stored_weight
    merged = [(x * n + y) / (n + 1) for x, y in zip(a, b)]
    norm = math.sqrt(sum(v * v for v in merged))
    if norm > 0:
        merged = [v / norm for v in merged]
    return _pack(merged)


class CandidatePool:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add_observation(
        self,
        trait_name: str,
        trait_value: str,
        category: str,
        evidence: str | None = None,
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
        arbitrary, so matching is semantic on the DESCRIPTION plus the
        EVIDENCE, never on the label.
        """
        if closed_vocabulary:
            await self._add_exact(trait_name, trait_value, category, evidence)
            return

        ts = time.time()
        vec_bytes = await embed(_embedding_text(trait_value, evidence))

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
                "UPDATE candidates SET trait_value = ?, evidence_count = ?, last_seen = ?,"
                " evidence_text = COALESCE(?, evidence_text) WHERE id = ?",
                (trait_value, new_count, ts, evidence, candidate_id),
            )
            async with self._conn.execute(
                "SELECT embedding FROM vec_candidates WHERE rowid = ?", (candidate_id,)
            ) as cur:
                stored = (await cur.fetchone())[0]
            await self._conn.execute(
                "UPDATE vec_candidates SET embedding = ? WHERE rowid = ?",
                (_merge_centroid(stored, vec_bytes, row[0]), candidate_id),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT id={candidate_id} → count={new_count}")
        else:
            cur = await self._conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category, evidence_text)"
                " VALUES (?, ?, 1, ?, ?, ?)",
                (trait_name, trait_value, ts, category, evidence),
            )
            candidate_rowid = cur.lastrowid
            await self._conn.execute(
                "INSERT INTO vec_candidates(rowid, embedding) VALUES (?, ?)",
                (candidate_rowid, vec_bytes),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT {trait_name!r} category={category!r}")

        await self._conn.commit()

    async def _add_exact(
        self,
        trait_name: str,
        trait_value: str,
        category: str,
        evidence: str | None = None,
    ) -> None:
        """Exact-name dedup, no embedding and no vec row.

        Evidence is recorded but deliberately plays no part in matching: this
        vocabulary is fixed, so the name is the identity.
        """
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, evidence_count FROM candidates WHERE trait_name = ?", (trait_name,)
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            await self._conn.execute(
                "INSERT INTO candidates (trait_name, trait_value, evidence_count, last_seen, category, evidence_text)"
                " VALUES (?, ?, 1, ?, ?, ?)",
                (trait_name, trait_value, ts, category, evidence),
            )
            print(f"[{datetime.now().isoformat()}] [CandidatePool] INSERT(exact) {trait_name!r}")
        else:
            new_count = row[1] + 1
            await self._conn.execute(
                "UPDATE candidates SET trait_value = ?, evidence_count = ?, last_seen = ?,"
                " evidence_text = COALESCE(?, evidence_text) WHERE id = ?",
                (trait_value, new_count, ts, evidence, row[0]),
            )
            print(
                f"[{datetime.now().isoformat()}] [CandidatePool] INCREMENT(exact) "
                f"{trait_name!r} → count={new_count}"
            )
        await self._conn.commit()

    async def get_candidates(self, min_evidence: int = 1) -> list[Candidate]:
        async with self._conn.execute(
            "SELECT id, trait_name, trait_value, evidence_count, last_seen, category, evidence_text"
            " FROM candidates WHERE evidence_count >= ? ORDER BY evidence_count DESC",
            (min_evidence,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Candidate(id=r[0], trait_name=r[1], trait_value=r[2], evidence_count=r[3],
                      last_seen=r[4], category=r[5], evidence_text=r[6])
            for r in rows
        ]
