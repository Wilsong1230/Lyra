"""Salience — which atoms mattered.

Reads `atoms` and `outcomes`, writes `atoms.salience`. Revisable by
construction: it recomputes every score from scratch, so a new outcome
re-scores the atoms around it. The old design computed salience once at write
time as `max()` over a batch, which saturated — 13 of 29 episodes sat at
0.933.

**Salience tracks the magnitude of an outcome's valence, not its sign.** A
failure that changed the approach is exactly as worth keeping as a first
success. Ranking good outcomes above bad ones would mean preferentially
forgetting what went wrong, which is the opposite of what a track record is
for, and it would hand the forgetting pass a bias nobody chose.

Nothing here is an affect judgement. Salience decides what survives
competition in retrieval; it is not a mood, and a low-valence outcome does not
make an atom less real.
"""
from __future__ import annotations

from datetime import datetime

from lyra_memory.config import (
    SALIENCE_BASELINE,
    SALIENCE_SESSION_SPILLOVER,
)
from lyra_memory.store.passes.cold import ColdPass


class SaliencePass(ColdPass):
    name = "salience"

    async def execute(self) -> int:
        # Preserve the outcome<->atom link in both directions. The old system
        # kept outcome -> trait and dropped the atom, which made it impossible
        # to ask what actually happened around a promotion.
        await self.store.db.execute(
            "UPDATE atoms SET outcome_id = ("
            "  SELECT o.id FROM outcomes o WHERE o.intent_atom_id = atoms.id"
            "  ORDER BY o.ts DESC LIMIT 1)"
        )

        # 1. Everything starts at the baseline. Nonzero, so that atoms with no
        #    measured outcome are still rankable against each other rather than
        #    being uniformly forgettable.
        await self.store.db.execute("UPDATE atoms SET salience = ?", (SALIENCE_BASELINE,))

        # 2. Session spillover: the turns around an attempt carry some of its
        #    weight. What made an outcome make sense is usually the exchange
        #    just before it, not the one line that triggered it.
        await self.store.db.execute(
            "UPDATE atoms SET salience = MAX(salience, ? * ("
            "  SELECT MAX(MIN(ABS(o.valence), 1.0)) FROM outcomes o"
            "  JOIN atoms ia ON ia.id = o.intent_atom_id"
            "  WHERE ia.session_id IS NOT NULL AND ia.session_id = atoms.session_id))"
            " WHERE session_id IS NOT NULL"
            "   AND EXISTS (SELECT 1 FROM outcomes o2 JOIN atoms ia2"
            "               ON ia2.id = o2.intent_atom_id"
            "               WHERE ia2.session_id = atoms.session_id)",
            (SALIENCE_SESSION_SPILLOVER,),
        )

        # 3. The atom the outcome is actually about carries its full weight.
        #    MIN/ABS clamp valence into [0, 1] — a caller writing 5.0 gets a
        #    bounded score, not a runaway one.
        await self.store.db.execute(
            "UPDATE atoms SET salience = ("
            "  SELECT MAX(MIN(ABS(o.valence), 1.0)) FROM outcomes o"
            "  WHERE o.intent_atom_id = atoms.id)"
            " WHERE EXISTS (SELECT 1 FROM outcomes o2 WHERE o2.intent_atom_id = atoms.id)"
        )

        await self.store.db.commit()

        async with self.store.db.execute(
            "SELECT COUNT(*) FROM atoms WHERE salience > ?", (SALIENCE_BASELINE,)
        ) as cur:
            (scored,) = await cur.fetchone()
        print(f"[{datetime.now().isoformat()}] [salience] {scored} atoms above baseline")
        return scored
