"""lyra_memory.outcomes — the outcome row, and salience scored from it.

THE LINK THAT WAS DROPPED. OutcomeConsolidator already turned outcomes into
candidate evidence, but the outcome itself was never persisted and never
pointed back at the atom that caused it. Without `intent_atom_id` the store can
say a trait has fifteen units of evidence and cannot say which fifteen moments
they were — the trajectory is unreconstructable and salience has nothing to
score against.

SALIENCE IS COLD AND REVISABLE. It used to be written once, at the hot path,
from a formula over the text alone; the spec makes it a batch pass that re-runs
when new outcomes arrive. What mattered about a moment is frequently not
knowable at the moment — it depends on how things turned out.

THE OUTCOME UNIT IS THE ATTEMPT, NOT THE COMMAND. Ten failed test runs then a
pass is ONE success. Abandoning is the failure. Scoring per command floors her
mood permanently during debugging and rebuilds exactly the "abandons under
frustration" topology problem the developmental loop exists to avoid.
"""
from __future__ import annotations

import math
import time

import aiosqlite

from lyra_memory.config import (
    EMOTION_KEYWORDS,
    SALIENCE_EMOTION_WEIGHT,
    SALIENCE_ENTITY_WEIGHT,
    SALIENCE_OUTCOME_WEIGHT,
    SALIENCE_RECENCY_HALFLIFE_DAYS,
)


class OutcomeStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def record(
        self,
        predicted: str,
        actual: str,
        *,
        intent_atom_id: int | None = None,
        valence: float | None = None,
        environment: str | None = None,
        ts: float | None = None,
    ) -> int:
        """Persist one resolved ATTEMPT and, when known, the atom that began it."""
        ts = time.time() if ts is None else ts
        cur = await self._conn.execute(
            "INSERT INTO outcomes (intent_atom_id, predicted, actual, valence, ts, environment)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (intent_atom_id, predicted, actual, valence, ts, environment),
        )
        outcome_id = cur.lastrowid
        if intent_atom_id is not None:
            # Preserve outcome<->atom in both directions: the atom carries the
            # outcome it led to, the outcome carries the atom that caused it.
            await self._conn.execute(
                "UPDATE atoms SET outcome_id = ? WHERE id = ?", (outcome_id, intent_atom_id)
            )
        await self._conn.commit()
        return outcome_id

    async def for_session(self, session_id: int) -> list[dict]:
        """Motor reflection reads THIS instead of atoms, per-session, reusing
        the segmentation boundaries. Same cold pass, different input."""
        async with self._conn.execute(
            "SELECT o.id, o.intent_atom_id, o.predicted, o.actual, o.valence, o.ts,"
            " o.environment FROM outcomes o JOIN atoms a ON a.id = o.intent_atom_id"
            " WHERE a.session_id = ? ORDER BY o.ts",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def recent(self, limit: int = 50) -> list[dict]:
        async with self._conn.execute(
            "SELECT id, intent_atom_id, predicted, actual, valence, ts, environment"
            " FROM outcomes ORDER BY ts DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [_row(r) for r in await cur.fetchall()]

    async def count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM outcomes") as cur:
            return (await cur.fetchone())[0]


class SalienceScorer:
    """Cold pass. Reads atoms + outcomes, writes atoms.salience.

    Revisable by construction: it recomputes every atom it looks at rather than
    skipping ones that already have a score, so a new outcome changes the
    salience of the moment that led to it.
    """

    def __init__(self, conn: aiosqlite.Connection, now: float | None = None) -> None:
        self._conn = conn
        self._now = now

    async def run(self, *, limit: int | None = None) -> int:
        now = time.time() if self._now is None else self._now
        sql = (
            "SELECT a.id, a.ts, a.text, a.outcome_id, o.predicted, o.actual, o.valence,"
            " (SELECT COUNT(*) FROM atom_entities ae WHERE ae.atom_id = a.id)"
            " FROM atoms a LEFT JOIN outcomes o ON o.id = a.outcome_id ORDER BY a.id"
        )
        args: tuple = ()
        if limit is not None:
            sql += " LIMIT ?"
            args = (limit,)
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()

        updates = []
        for atom_id, ts, text, outcome_id, predicted, actual, valence, n_entities in rows:
            updates.append((score_atom(
                ts=ts, text=text, now=now, has_outcome=outcome_id is not None,
                predicted=predicted, actual=actual, valence=valence,
                entity_count=n_entities,
            ), atom_id))

        await self._conn.executemany("UPDATE atoms SET salience = ? WHERE id = ?", updates)
        await self._conn.commit()
        return len(updates)


def score_atom(
    *,
    ts: float,
    text: str,
    now: float,
    has_outcome: bool = False,
    predicted: str | None = None,
    actual: str | None = None,
    valence: float | None = None,
    entity_count: int = 0,
) -> float:
    """Pure. Age x consequence x emotional load x entity density, in [0, 1].

    An atom that led to a resolved outcome mattered more than one that did not,
    and one whose outcome SURPRISED her — predicted != actual — mattered most.
    Prediction error is the only thing here that could not have been known at
    write time, which is the whole reason this pass is cold.
    """
    half_life = SALIENCE_RECENCY_HALFLIFE_DAYS * 86400
    age = max(0.0, now - ts)
    recency = math.pow(0.5, age / half_life) if half_life > 0 else 0.0

    consequence = 0.0
    if has_outcome:
        consequence = 0.5
        if predicted is not None and actual is not None and predicted != actual:
            consequence = 1.0        # prediction error
        if valence is not None:
            consequence = min(1.0, consequence + abs(valence) * 0.25)

    lowered = text.lower()
    emotion = 1.0 if any(w in lowered for w in EMOTION_KEYWORDS) else 0.0
    entities = min(entity_count / 3.0, 1.0)

    base_weight = 1.0 - (
        SALIENCE_OUTCOME_WEIGHT + SALIENCE_EMOTION_WEIGHT + SALIENCE_ENTITY_WEIGHT
    )
    score = (
        base_weight * recency
        + SALIENCE_OUTCOME_WEIGHT * consequence
        + SALIENCE_EMOTION_WEIGHT * emotion
        + SALIENCE_ENTITY_WEIGHT * entities
    )
    return max(0.0, min(1.0, score))


def _row(r) -> dict:
    return {
        "id": r[0], "intent_atom_id": r[1], "predicted": r[2], "actual": r[3],
        "valence": r[4], "ts": r[5], "environment": r[6],
    }
