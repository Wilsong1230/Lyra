from __future__ import annotations
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, RETRIEVAL_TRAIT_LIMIT
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.models import Trait


async def assert_trait_history_integrity(conn: aiosqlite.Connection) -> None:
    """Every trait's current state must match its latest trait_history row.

    A trait with no history row, or whose confidence/stability disagree with
    the newest history entry, means something wrote to `traits` outside
    IdentityEngine. Prevention can fail silently; this is the detection.
    """
    async with conn.execute(
        "SELECT t.id, t.name, t.confidence, t.stability, h.conf_after, h.tier_after"
        " FROM traits t LEFT JOIN trait_history h ON h.id ="
        "   (SELECT id FROM trait_history WHERE trait_id = t.id ORDER BY id DESC LIMIT 1)"
    ) as cur:
        rows = await cur.fetchall()
    for tid, name, conf, tier, h_conf, h_tier in rows:
        assert h_conf is not None or h_tier is not None, (
            f"trait {name!r} (id={tid}) has no trait_history row — "
            "something mutated traits outside IdentityEngine"
        )
        assert h_conf == conf and h_tier == tier, (
            f"trait {name!r} (id={tid}) state ({conf}, {tier!r}) does not match "
            f"its latest history row ({h_conf}, {h_tier!r}) — "
            "something mutated traits outside IdentityEngine"
        )


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

    async def consolidate(self, dream_id: int | None = None) -> None:
        candidates = await self._pool.get_candidates(min_evidence=1)
        for c in candidates:
            stability = self._stability_for(c.evidence_count)
            if stability is None:
                continue
            confidence = min(c.evidence_count / TRAIT_THRESHOLDS["core"], 1.0)
            await self._upsert_trait(
                c.trait_name, c.trait_value, confidence, stability, c.evidence_count,
                dream_id=dream_id,
            )
        await assert_trait_history_integrity(self._conn)

    async def _upsert_trait(
        self,
        name: str,
        value: str,
        confidence: float,
        stability: str,
        evidence_count: int,
        dream_id: int | None = None,
    ) -> None:
        """Write or update a trait.

        Hard rule: never mutate a trait without a trait_history row in the
        same transaction. Both statements run before the single commit below;
        aiosqlite's implicit transaction makes them atomic. No mutation
        (write-protected core, or a no-op update) → no history row.
        """
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, value, confidence, stability, evidence_count FROM traits WHERE name = ?",
            (name,),
        ) as cur:
            row = await cur.fetchone()

        if row:
            trait_id, old_value, old_conf, old_tier, old_evidence = row
            if old_tier == "core" and old_conf >= CORE_CONFIDENCE_LOCK:
                return  # write-protected: no mutation, no history
            if (old_value, old_conf, old_tier, old_evidence) == (value, confidence, stability, evidence_count):
                return  # no-op: no mutation, no history
            await self._conn.execute(
                "UPDATE traits SET value=?, confidence=?, stability=?, evidence_count=?, updated_at=? WHERE id=?",
                (value, confidence, stability, evidence_count, ts, trait_id),
            )
            event = "tier_change" if stability != old_tier else "confidence_change"
            await self._conn.execute(
                "INSERT INTO trait_history"
                " (ts, trait_id, trait_label, event, conf_before, conf_after,"
                "  tier_before, tier_after, evidence_count, dream_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, trait_id, name, event, old_conf, confidence,
                 old_tier, stability, evidence_count, dream_id),
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] UPDATE {name!r} stability={stability!r} confidence={confidence:.2f}")
        else:
            cur = await self._conn.execute(
                "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (name, value, confidence, stability, evidence_count, ts),
            )
            trait_id = cur.lastrowid
            await self._conn.execute(
                "INSERT INTO trait_history"
                " (ts, trait_id, trait_label, event, conf_before, conf_after,"
                "  tier_before, tier_after, evidence_count, dream_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, trait_id, name, "promoted", None, confidence,
                 None, stability, evidence_count, dream_id),
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
