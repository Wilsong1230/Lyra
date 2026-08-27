"""Forgetting — demotion, not deletion.

A store where everything survives at equal weight has no shape. Compression is
the mechanism that produces character: what survives is what mattered.

**Storage never shrinks; only competition does.** An atom below the threshold
is excluded from the KNN pool and stays queryable by time, by session, by
entity, and by exact id. Nothing is ever removed, and there is no code path
here that deletes a row.

Facts are not in this pool at all. That is routing, not an exemption: facts
live in their own table with their own physics — exact retrieval, no decay,
correction by supersession — so no "except facts" branch is needed anywhere.
The net effect is that she forgets what a Tuesday felt like and does not
forget where you go to school.

The curve is `exp(-age / stability)`, where stability grows with salience and
with how often the atom has actually been retrieved. Access is itself evidence
of relevance, so retrieval resets the clock — an atom that keeps proving
useful keeps being available, which is the spacing effect and not a coincidence.

**This pass needs a large store to tune against.** Every constant is
provisional and none has been fitted to real data; the sheet says as much, and
`review_forgetting` exists so the first tuning is done by reading what got
demoted rather than by adjusting numbers until the histogram looks nice.
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from lyra_memory.config import (
    FORGET_ACCESS_WEIGHT,
    FORGET_BASE_STABILITY_DAYS,
    FORGET_SALIENCE_WEIGHT,
    FORGET_THRESHOLD,
    SALIENCE_BASELINE,
)
from lyra_memory.store.passes.cold import ColdPass

_DAY = 86400.0


def retrievability(
    age_days: float,
    salience: float,
    access_count: int,
) -> float:
    """The forgetting curve for one atom. Pure arithmetic, no I/O, no LLM.

    Salience and access both buy *stability* rather than adding to the score
    directly: a memorable thing does not start out more retrievable, it decays
    more slowly. Access is logarithmic — the tenth retrieval says much less
    than the second.
    """
    stability = (
        FORGET_BASE_STABILITY_DAYS
        * (1.0 + max(salience, 0.0) * FORGET_SALIENCE_WEIGHT)
        * (1.0 + math.log1p(max(access_count, 0)) * FORGET_ACCESS_WEIGHT)
    )
    return math.exp(-max(age_days, 0.0) / stability)


class ForgettingPass(ColdPass):
    """Weekly. Cheap — arithmetic over three columns and the context log."""

    name = "forgetting"

    def __init__(self, store, backup_dir=None, now: float | None = None) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._now = now

    async def execute(self) -> int:
        now = self._now if self._now is not None else time.time()

        # Access history comes from context_log — what was actually injected,
        # which is the only honest record of an atom having been used. No new
        # counter to keep in sync, and it is already written on every turn.
        async with self.store.db.execute(
            "SELECT CAST(j.value AS INTEGER) AS atom_id, COUNT(*), MAX(c.ts)"
            " FROM context_log c, json_each(c.atom_ids) j"
            " GROUP BY atom_id"
        ) as cur:
            access = {row[0]: (row[1], row[2]) for row in await cur.fetchall()}

        async with self.store.db.execute(
            "SELECT id, ts, salience FROM atoms"
        ) as cur:
            atoms = await cur.fetchall()

        updates = []
        for atom_id, ts, salience in atoms:
            count, last_access = access.get(atom_id, (0, None))
            # Retrieval resets the clock: access is evidence of relevance, so
            # age runs from the last time the atom was actually used.
            reference = max(ts, last_access) if last_access is not None else ts
            score = retrievability(
                age_days=(now - reference) / _DAY,
                salience=salience if salience is not None else SALIENCE_BASELINE,
                access_count=count,
            )
            updates.append((score, atom_id))

        await self.store.db.executemany(
            "UPDATE atoms SET retrievability = ? WHERE id = ?", updates)
        await self.store.db.commit()

        demoted = sum(1 for score, _ in updates if score < FORGET_THRESHOLD)
        print(f"[{datetime.now().isoformat()}] [forgetting] scored {len(updates)} atoms; "
              f"{demoted} below {FORGET_THRESHOLD} and out of the KNN pool "
              f"(still queryable by time, session, entity, id)")
        return demoted
