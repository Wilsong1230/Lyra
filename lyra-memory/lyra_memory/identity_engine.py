from __future__ import annotations
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, RETRIEVAL_TRAIT_LIMIT
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.models import Trait


class IdentityEngine:
    def __init__(self, conn: aiosqlite.Connection, candidate_pool: CandidatePool) -> None:
        self._conn = conn
        self._pool = candidate_pool

    def _stability_for(self, count: int) -> str | None:
        if count >= TRAIT_THRESHOLDS["core"]:
            return "core"
        if count >= TRAIT_THRESHOLDS["character"]:
            return "character"
        if count >= TRAIT_THRESHOLDS["surface"]:
            return "surface"
        return None

    async def consolidate(self) -> None:
        candidates = await self._pool.get_candidates(min_evidence=1)
        for c in candidates:
            stability = self._stability_for(c.evidence_count)
            if stability is None:
                continue
            confidence = min(c.evidence_count / TRAIT_THRESHOLDS["core"], 1.0)
            await self._upsert_trait(c.trait_name, c.trait_value, confidence, stability, c.evidence_count)

    async def _upsert_trait(self, name: str, value: str, confidence: float, stability: str, evidence_count: int) -> None:
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, stability, confidence FROM traits WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            existing_stability, existing_confidence = row[1], row[2]
            if existing_stability == "core" and existing_confidence >= CORE_CONFIDENCE_LOCK:
                return  # write-protected
            await self._conn.execute(
                "UPDATE traits SET value=?, confidence=?, stability=?, evidence_count=?, updated_at=? WHERE id=?",
                (value, confidence, stability, evidence_count, ts, row[0]),
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] UPDATE {name!r} stability={stability!r} confidence={confidence:.2f}")
        else:
            await self._conn.execute(
                "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (name, value, confidence, stability, evidence_count, ts),
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] PROMOTE {name!r} stability={stability!r}")

        await self._conn.commit()

    async def get_top_traits(self, limit: int = RETRIEVAL_TRAIT_LIMIT) -> list[Trait]:
        async with self._conn.execute(
            "SELECT id, name, value, confidence, stability, evidence_count, updated_at"
            " FROM traits ORDER BY confidence DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Trait(id=r[0], name=r[1], value=r[2], confidence=r[3], stability=r[4], evidence_count=r[5], updated_at=r[6])
            for r in rows
        ]
