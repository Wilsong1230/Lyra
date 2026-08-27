"""lyra_memory.identity_engine — promotion, and the trajectory that records it.

`traits` holds the endpoint. `trait_history` holds the trajectory, and the
trajectory is the primary artifact of the project. It was previously stored
nowhere: every promotion that happened before the table existed is simply lost.

THE RULE: IdentityEngine never mutates a trait without writing a
`trait_history` row in the SAME TRANSACTION. Append-only — history rows are
never updated or deleted. `traits` is a materialized view of the latest state;
history is the source of truth.

What it buys:
  * "I used to be more X" — reportable, not authored
  * rate of change, not just current value
  * which dream caused which promotion (dream_id)
  * replay: rebuild `traits` at any past timestamp
  * churn detection — a trait oscillating is a tuning bug, invisible otherwise

DETECTION LAYER. Prevention can fail silently; detection tells you it did. A
trait whose current value has no corresponding history row means something
wrote outside the path — a direct `sqlite3` write, which the filesystem
boundary is supposed to make impossible. `audit()` is that check.
"""
from __future__ import annotations
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import (
    TRAIT_THRESHOLDS, CORE_CONFIDENCE_LOCK, RETRIEVAL_TRAIT_LIMIT,
    TRAIT_CONFIDENCE_FLOOR,
)
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.models import Trait, TraitHistoryRow


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
                c.trait_name, c.trait_value, confidence, stability,
                c.evidence_count, dream_id=dream_id,
            )

    async def _upsert_trait(
        self,
        name: str,
        value: str,
        confidence: float,
        stability: str,
        evidence_count: int,
        *,
        dream_id: int | None = None,
    ) -> None:
        """Mutate a trait and record the mutation. One transaction, or neither.

        The history write is not a log line after the fact — it is part of the
        same commit as the change it describes, so a crash between them is not
        a reachable state.
        """
        ts = time.time()
        async with self._conn.execute(
            "SELECT id, stability, confidence FROM traits WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()

        if row:
            trait_id, tier_before, conf_before = row
            if tier_before == "core" and conf_before >= CORE_CONFIDENCE_LOCK:
                return  # write-protected
            if conf_before == confidence and tier_before == stability:
                return  # nothing changed; an unchanged trait writes no history

            await self._conn.execute(
                "UPDATE traits SET value=?, confidence=?, stability=?, evidence_count=?,"
                " updated_at=? WHERE id=?",
                (value, confidence, stability, evidence_count, ts, trait_id),
            )
            await self._write_history(
                ts=ts, trait_id=trait_id, trait_label=name,
                event="tier_change" if tier_before != stability else "confidence_change",
                conf_before=conf_before, conf_after=confidence,
                tier_before=tier_before, tier_after=stability,
                evidence_count=evidence_count, dream_id=dream_id,
            )
            print(
                f"[{datetime.now().isoformat()}] [IdentityEngine] UPDATE {name!r} "
                f"stability={stability!r} confidence={confidence:.2f}"
            )
        else:
            cur2 = await self._conn.execute(
                "INSERT INTO traits (name, value, confidence, stability, evidence_count, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (name, value, confidence, stability, evidence_count, ts),
            )
            await self._write_history(
                ts=ts, trait_id=cur2.lastrowid, trait_label=name, event="promoted",
                conf_before=None, conf_after=confidence,
                tier_before=None, tier_after=stability,
                evidence_count=evidence_count, dream_id=dream_id,
            )
            print(f"[{datetime.now().isoformat()}] [IdentityEngine] PROMOTE {name!r} stability={stability!r}")

        await self._conn.commit()

    async def _write_history(self, **kw) -> None:
        await self._conn.execute(
            "INSERT INTO trait_history (ts, trait_id, trait_label, event, conf_before,"
            " conf_after, tier_before, tier_after, evidence_count, dream_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                kw["ts"], kw["trait_id"], kw["trait_label"], kw["event"],
                kw["conf_before"], kw["conf_after"], kw["tier_before"],
                kw["tier_after"], kw["evidence_count"], kw["dream_id"],
            ),
        )

    async def get_top_traits(self, limit: int = RETRIEVAL_TRAIT_LIMIT) -> list[Trait]:
        async with self._conn.execute(
            "SELECT id, name, value, confidence, stability, evidence_count, updated_at"
            " FROM traits ORDER BY confidence DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [_trait(r) for r in rows]

    async def get_context_traits(self, limit: int = RETRIEVAL_TRAIT_LIMIT) -> list[Trait]:
        """The retrieval block: conf >= 0.3, per the pinned assembly order."""
        async with self._conn.execute(
            "SELECT id, name, value, confidence, stability, evidence_count, updated_at"
            " FROM traits WHERE confidence >= ? ORDER BY confidence DESC LIMIT ?",
            (TRAIT_CONFIDENCE_FLOOR, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [_trait(r) for r in rows]

    async def history(self, trait_label: str | None = None, limit: int = 100) -> list[TraitHistoryRow]:
        """Not injected by default — it costs tokens and is rarely relevant per
        turn. Injected only when the turn is about her own change, and read by
        the dream cycle, where reflecting on one's own drift is legitimate."""
        if trait_label is None:
            sql, args = (
                "SELECT id, ts, trait_id, trait_label, event, conf_before, conf_after,"
                " tier_before, tier_after, evidence_count, dream_id FROM trait_history"
                " ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        else:
            sql, args = (
                "SELECT id, ts, trait_id, trait_label, event, conf_before, conf_after,"
                " tier_before, tier_after, evidence_count, dream_id FROM trait_history"
                " WHERE trait_label = ? ORDER BY ts DESC LIMIT ?",
                (trait_label, limit),
            )
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        return [
            TraitHistoryRow(
                id=r[0], ts=r[1], trait_id=r[2], trait_label=r[3], event=r[4],
                conf_before=r[5], conf_after=r[6], tier_before=r[7],
                tier_after=r[8], evidence_count=r[9], dream_id=r[10],
            )
            for r in rows
        ]

    async def replay(self, at_ts: float) -> dict[str, dict]:
        """Rebuild `traits` as of any past timestamp, from history alone.

        This is what makes history the source of truth rather than a log: if the
        two ever disagree, this is the one that is right.
        """
        async with self._conn.execute(
            "SELECT trait_label, conf_after, tier_after, evidence_count, ts"
            " FROM trait_history WHERE ts <= ? ORDER BY ts ASC",
            (at_ts,),
        ) as cur:
            rows = await cur.fetchall()
        state: dict[str, dict] = {}
        for label, conf, tier, evidence, ts in rows:
            state[label] = {
                "name": label, "confidence": conf, "stability": tier,
                "evidence_count": evidence, "updated_at": ts,
            }
        return state

    async def audit(self) -> list[str]:
        """Detection layer: traits with no history behind them.

        Prevention (the filesystem boundary) can fail silently. A trait whose
        current value has no corresponding history row means something wrote
        outside the sanctioned path.
        """
        async with self._conn.execute(
            "SELECT t.name FROM traits t LEFT JOIN trait_history h"
            " ON h.trait_label = t.name WHERE h.id IS NULL"
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def churn(self, trait_label: str) -> int:
        """Count tier reversals. A trait oscillating is a tuning bug, and it is
        invisible without history."""
        rows = await self.history(trait_label, limit=1000)
        tiers = [r.tier_after for r in reversed(rows) if r.tier_after]
        return sum(1 for a, b in zip(tiers, tiers[1:]) if a != b)


def _trait(r) -> Trait:
    return Trait(
        id=r[0], name=r[1], value=r[2], confidence=r[3], stability=r[4],
        evidence_count=r[5], updated_at=r[6],
    )
